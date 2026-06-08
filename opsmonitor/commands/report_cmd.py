import json
import csv
from collections import Counter
from datetime import datetime, timedelta
from io import StringIO
import click
from opsmonitor.config import ConfigManager, ValidationError
from opsmonitor.formatter import OutputFormatter, percentile, p95, p99, format_duration, Colors


def _calculate_stats(history_entries):
    """计算统计数据"""
    success_times = []
    failed_times = []
    total_count = len(history_entries)
    success_count = 0
    failed_count = 0
    min_time = None
    max_time = None

    for entry in history_entries:
        if entry.get("success"):
            success_count += 1
            rt = entry.get("response_time")
            if rt is not None:
                success_times.append(rt)
                if min_time is None or rt < min_time:
                    min_time = rt
                if max_time is None or rt > max_time:
                    max_time = rt
        else:
            failed_count += 1
            rt = entry.get("response_time")
            if rt is not None:
                failed_times.append(rt)

    avg_time = sum(success_times) / len(success_times) if success_times else 0
    median_time = sorted(success_times)[len(success_times) // 2] if success_times else 0
    p95_time = p95(success_times) if success_times else 0
    p99_time = p99(success_times) if success_times else 0
    success_rate = (success_count / total_count * 100) if total_count > 0 else 0

    return {
        "total_count": total_count,
        "success_count": success_count,
        "failed_count": failed_count,
        "success_rate": success_rate,
        "avg_response_ms": avg_time,
        "median_response_ms": median_time,
        "p95_response_ms": p95_time,
        "p99_response_ms": p99_time,
        "min_response_ms": min_time,
        "max_response_ms": max_time,
        "all_response_times": success_times,
    }


def _parse_timestamp(ts):
    """解析时间戳，支持int和str格式"""
    if ts is None:
        return None
    if isinstance(ts, (int, float)):
        return datetime.fromtimestamp(int(ts))
    if isinstance(ts, str):
        try:
            return datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            try:
                return datetime.fromtimestamp(int(ts))
            except ValueError:
                return None
    return None


def _calculate_outage_durations(history_entries):
    """计算故障时长 - 返回最长和平均故障时长（秒）"""
    if not history_entries:
        return 0, 0

    current_failure_start = None
    max_duration = 0
    total_duration = 0
    failure_count = 0

    sorted_entries = sorted(
        history_entries,
        key=lambda x: _parse_timestamp(x.get("timestamp", 0)) or datetime.fromtimestamp(0)
    )

    for entry in sorted_entries:
        ts = entry.get("timestamp")
        dt = _parse_timestamp(ts)
        if dt is None:
            continue

        if not entry.get("success"):
            if current_failure_start is None:
                current_failure_start = dt
        else:
            if current_failure_start is not None:
                duration = (dt - current_failure_start).total_seconds()
                max_duration = max(max_duration, duration)
                total_duration += duration
                failure_count += 1
                current_failure_start = None

    if current_failure_start is not None:
        last_dt = None
        for entry in reversed(sorted_entries):
            last_dt = _parse_timestamp(entry.get("timestamp"))
            if last_dt:
                break
        if last_dt:
            duration = (last_dt - current_failure_start).total_seconds()
            max_duration = max(max_duration, duration)
            total_duration += duration
            failure_count += 1

    avg_duration = total_duration / failure_count if failure_count > 0 else 0
    return max_duration, avg_duration


def _generate_report_data(cm, hours=24):
    """生成完整的报表数据"""
    end_time = datetime.now()
    start_time = end_time - timedelta(hours=hours)

    config = cm.load_config()
    all_history = cm.get_history(start_time=start_time, end_time=end_time, limit=10000)
    all_alerts = cm.get_alerts()
    all_events = cm.get_events(limit=100)

    target_stats = {}
    for target_name in config["targets"]:
        target_history = [h for h in all_history if h.get("target") == target_name]
        stats = _calculate_stats(target_history)
        max_outage, avg_outage = _calculate_outage_durations(target_history)
        stats["longest_outage_sec"] = max_outage
        stats["avg_outage_sec"] = avg_outage
        target_stats[target_name] = stats

    group_stats = {}
    for group_name, target_names in config["groups"].items():
        group_history = []
        for t in target_names:
            if t in config["targets"]:
                group_history.extend([h for h in all_history if h.get("target") == t])
        gstats = _calculate_stats(group_history)

        target_max_outages = []
        target_avg_outages = []
        for t in target_names:
            if t in target_stats:
                target_max_outages.append(target_stats[t]["longest_outage_sec"])
                target_avg_outages.append(target_stats[t]["avg_outage_sec"])

        gstats["longest_outage_sec"] = max(target_max_outages) if target_max_outages else 0
        gstats["avg_outage_sec"] = sum(target_avg_outages) / len(target_avg_outages) if target_avg_outages else 0
        group_stats[group_name] = gstats

    failure_ranking = sorted(
        [
            {
                "target": t,
                "failures": s["failed_count"],
                "longest_outage": s.get("longest_outage_sec", 0),
                "total_downtime": s.get("avg_outage_sec", 0) * s.get("failed_count", 0)
            }
            for t, s in target_stats.items()
            if s["failed_count"] > 0
        ],
        key=lambda x: x["failures"],
        reverse=True
    )

    return {
        "period": {
            "start_time": start_time.strftime("%Y-%m-%d %H:%M:%S"),
            "end_time": end_time.strftime("%Y-%m-%d %H:%M:%S"),
            "hours": hours
        },
        "targets": target_stats,
        "groups": group_stats,
        "failure_ranking": failure_ranking,
        "alerts_count": len(all_alerts),
        "unhandled_alerts_count": sum(1 for a in all_alerts if not a.get("handled")),
        "events_count": len(all_events),
        "active_events_count": sum(1 for e in all_events if not e.get("closed", False)),
        "config": config
    }


def _generate_text_report(report_data, formatter, quiet=False, verbose=False):
    """生成文本报表"""
    output = []

    if not quiet:
        period = report_data["period"]
        output.append(formatter.format_report_section(
            "值班报告",
            f"统计周期: {period['start_time']} 至 {period['end_time']} ({period['hours']} 小时)"
        ))
        output.append("")

        total_targets = len(report_data["targets"])
        total_checks = sum(s["total_count"] for s in report_data["targets"].values())
        total_success = sum(s["success_count"] for s in report_data["targets"].values())
        overall_rate = (total_success / total_checks * 100) if total_checks > 0 else 0

        output.append(formatter.format_report_section(
            "整体概览",
            f"监控目标: {total_targets} 个 | 总检查: {total_checks} 次 | 整体可用率: {overall_rate:.1f}% | "
            f"告警: {report_data['alerts_count']} 条 | 未处理: {report_data['unhandled_alerts_count']} 条 | "
            f"持续事件: {report_data['events_count']} 条 (进行中 {report_data['active_events_count']} 条)"
        ))
        output.append("")

    if not quiet:
        output.append(formatter.format_report_section("按服务组统计", ""))
        for group_name, gdata in sorted(report_data["groups"].items()):
            targets = report_data["config"]["groups"].get(group_name, [])
            target_count = len([t for t in targets if t in report_data["config"]["targets"]])

            if gdata["total_count"] == 0:
                continue

            status_color = "\033[92m" if gdata["success_rate"] >= 99.9 else "\033[93m" if gdata["success_rate"] >= 95 else "\033[91m"
            success_rate_str = f"{gdata['success_rate']:.1f}%"
            availability_str = formatter.format_availability(f"{group_name} ({target_count}个目标)", gdata["success_count"], gdata["total_count"])

            output.append(availability_str)
            if not quiet:
                output.append(f"  成功率: {formatter._colorize(success_rate_str, status_color)}")
                output.append(formatter.format_stats("响应时间", gdata["all_response_times"], "ms"))
                longest_outage_str = format_duration(gdata["longest_outage_sec"]) if gdata["longest_outage_sec"] > 0 else "无"
                output.append(f"  最长故障时长: {longest_outage_str}")
                output.append("")

    if report_data["failure_ranking"] and not quiet:
        output.append(formatter.format_failure_ranking(report_data["failure_ranking"]))
        output.append("")

    if verbose and not quiet:
        output.append(formatter.format_report_section("各目标详细统计", ""))
        for target_name, tdata in sorted(report_data["targets"].items()):
            if tdata["total_count"] == 0:
                continue

            target_config = report_data["config"]["targets"].get(target_name, {})
            status_color = "\033[92m" if tdata["success_rate"] >= 99.9 else "\033[93m" if tdata["success_rate"] >= 95 else "\033[91m"
            success_rate_str = f"{tdata['success_rate']:.1f}%"

            output.append(f"\n{target_name} ({target_config.get('type', 'http')}://{target_config.get('address', '')})")
            output.append(f"  检查次数: {tdata['total_count']} | 成功: {tdata['success_count']} | 失败: {tdata['failed_count']} | 成功率: {formatter._colorize(success_rate_str, status_color)}")
            output.append(formatter.format_stats("响应时间", tdata["all_response_times"], "ms"))
            longest_outage_str = format_duration(tdata["longest_outage_sec"]) if tdata["longest_outage_sec"] > 0 else "无"
            avg_outage_str = format_duration(tdata["avg_outage_sec"]) if tdata["avg_outage_sec"] > 0 else "无"
            output.append(f"  最长故障时长: {longest_outage_str} | 平均故障时长: {avg_outage_str}")

    if quiet:
        total_targets = len(report_data["targets"])
        total_checks = sum(s["total_count"] for s in report_data["targets"].values())
        total_success = sum(s["success_count"] for s in report_data["targets"].values())
        overall_rate = (total_success / total_checks * 100) if total_checks > 0 else 0
        worst = report_data["failure_ranking"][0] if report_data["failure_ranking"] else None
        worst_str = f" | 故障最多: {worst['target']} ({worst['failures']}次)" if worst else ""
        output.append(formatter._colorize(
            f"报告: {total_targets}目标 | {total_checks}次检查 | 可用率 {overall_rate:.1f}% | {report_data['unhandled_alerts_count']}条未处理告警{worst_str}",
            "\033[94m"
        ))

    return "\n".join(output)


def _generate_json_report(report_data):
    """生成JSON报表"""
    def _convert_for_json(data):
        if isinstance(data, dict):
            return {k: _convert_for_json(v) for k, v in data.items() if k != "all_response_times"}
        elif isinstance(data, list):
            return [_convert_for_json(item) for item in data]
        return data

    clean_data = _convert_for_json(report_data)
    return json.dumps(clean_data, indent=2, ensure_ascii=False)


def _generate_csv_report(report_data):
    """生成CSV报表"""
    output = StringIO()
    writer = csv.writer(output)

    writer.writerow([
        "目标名称", "服务组", "检查次数", "成功次数", "失败次数", "成功率%",
        "平均响应ms", "中位数ms", "P95ms", "P99ms", "最小响应ms", "最大响应ms",
        "最长故障时长s", "平均故障时长s"
    ])

    config = report_data["config"]
    for target_name, tdata in sorted(report_data["targets"].items()):
        target_config = config["targets"].get(target_name, {})
        group = target_config.get("group", "default")
        writer.writerow([
            target_name,
            group,
            tdata["total_count"],
            tdata["success_count"],
            tdata["failed_count"],
            f"{tdata['success_rate']:.2f}",
            f"{tdata['avg_response_ms']:.2f}",
            f"{tdata['median_response_ms']:.2f}",
            f"{tdata['p95_response_ms']:.2f}",
            f"{tdata['p99_response_ms']:.2f}",
            tdata["min_response_ms"] or "",
            tdata["max_response_ms"] or "",
            f"{tdata['longest_outage_sec']:.1f}",
            f"{tdata['avg_outage_sec']:.1f}"
        ])

    writer.writerow([])
    writer.writerow(["=== 按服务组统计 ==="])
    writer.writerow([
        "服务组", "目标数", "检查次数", "成功次数", "失败次数", "成功率%",
        "平均响应ms", "中位数ms", "P95ms", "P99ms", "最长故障时长s", "平均故障时长s"
    ])

    for group_name, gdata in sorted(report_data["groups"].items()):
        targets = config["groups"].get(group_name, [])
        target_count = len([t for t in targets if t in config["targets"]])
        writer.writerow([
            group_name,
            target_count,
            gdata["total_count"],
            gdata["success_count"],
            gdata["failed_count"],
            f"{gdata['success_rate']:.2f}",
            f"{gdata['avg_response_ms']:.2f}",
            f"{gdata['median_response_ms']:.2f}",
            f"{gdata['p95_response_ms']:.2f}",
            f"{gdata['p99_response_ms']:.2f}",
            f"{gdata['longest_outage_sec']:.1f}",
            f"{gdata['avg_outage_sec']:.1f}"
        ])

    writer.writerow([])
    writer.writerow(["=== 故障次数排行 ==="])
    writer.writerow(["排名", "目标名称", "故障次数"])

    for i, item in enumerate(report_data["failure_ranking"], 1):
        writer.writerow([i, item["target"], item["failures"]])

    return output.getvalue()


@click.group()
def report():
    """报表生成"""
    pass


@report.command()
@click.option("--hours", type=int, default=24, help="统计时长（小时）")
@click.option("--group", help="按服务组筛选")
@click.option("--config-dir", type=click.Path(), help="配置目录路径")
@click.pass_context
def overview(ctx, hours, group, config_dir):
    """生成值班概览报告"""
    from pathlib import Path
    cm = ConfigManager(Path(config_dir)) if config_dir else ConfigManager()

    verbose = ctx.obj.get("verbose", False)
    quiet = ctx.obj.get("quiet", False)
    formatter = OutputFormatter(verbose=verbose, quiet=quiet)

    report_data = _generate_report_data(cm, hours=hours)

    if group:
        if group not in report_data["groups"]:
            raise ValidationError(f"服务组 '{group}' 不存在")
        filtered_targets = {}
        for t in report_data["config"]["groups"].get(group, []):
            if t in report_data["targets"]:
                filtered_targets[t] = report_data["targets"][t]
        report_data["targets"] = filtered_targets
        filtered_groups = {group: report_data["groups"][group]}
        report_data["groups"] = filtered_groups

    text_report = _generate_text_report(report_data, formatter, quiet=quiet, verbose=verbose)
    click.echo(text_report)


@report.command()
@click.option("--hours", type=int, default=24, help="统计时长（小时）")
@click.option("--output", "-o", type=click.Path(), help="输出文件路径")
@click.option("--format", "fmt", type=click.Choice(["json", "csv"]), default="json", help="输出格式")
@click.option("--config-dir", type=click.Path(), help="配置目录路径")
@click.pass_context
def export(ctx, hours, output, fmt, config_dir):
    """导出值班报告"""
    from pathlib import Path
    cm = ConfigManager(Path(config_dir)) if config_dir else ConfigManager()

    verbose = ctx.obj.get("verbose", False)
    quiet = ctx.obj.get("quiet", False)
    formatter = OutputFormatter(verbose=verbose, quiet=quiet)

    if hours <= 0:
        raise ValidationError("统计时长必须大于 0 小时")

    report_data = _generate_report_data(cm, hours=hours)

    if fmt == "json":
        content = _generate_json_report(report_data)
    else:
        content = _generate_csv_report(report_data)

    if output:
        with open(output, "w", encoding="utf-8") as f:
            f.write(content)
        click.echo(formatter._colorize(f"✅ 报告已导出到 {output} ({fmt} 格式)", "\033[92m"))
    else:
        click.echo(content)


@report.command("timeline")
@click.option("--target", help="按目标筛选")
@click.option("--group", help="按服务组筛选")
@click.option("--hours", type=int, default=24, help="统计时长（小时）")
@click.option("--only-active", is_flag=True, help="仅显示进行中的事件")
@click.option("--config-dir", type=click.Path(), help="配置目录路径")
@click.pass_context
def timeline(ctx, target, group, hours, only_active, config_dir):
    """按持续事件生成故障时间线"""
    from pathlib import Path
    cm = ConfigManager(Path(config_dir)) if config_dir else ConfigManager()

    verbose = ctx.obj.get("verbose", False)
    quiet = ctx.obj.get("quiet", False)
    formatter = OutputFormatter(verbose=verbose, quiet=quiet)

    if hours <= 0:
        raise ValidationError("统计时长必须大于 0 小时")

    end_time = datetime.now()
    start_time = end_time - timedelta(hours=hours)
    now_ts = int(end_time.timestamp())

    events = cm.get_events(
        target=target,
        group=group,
        only_active=only_active,
        start_time=start_time,
        end_time=end_time,
        limit=100,
        impact_window=True
    )

    if not events:
        click.echo(formatter._colorize("✅ 该时间段内无故障事件", "\033[92m"))
        return

    if not quiet:
        filters = []
        if target:
            filters.append(f"目标={target}")
        if group:
            filters.append(f"组={group}")
        if only_active:
            filters.append("仅进行中")
        filter_str = f" (筛选: {', '.join(filters)})" if filters else ""
        click.echo(formatter._colorize(f"⏱️  故障时间线 ({hours}小时内) - 按影响窗口{filter_str}:", "\033[94m"))
        click.echo("-" * 110)

    def _get_real_duration(event):
        if event.get("closed", False) and event.get("duration_seconds"):
            return event.get("duration_seconds", 0)
        start_ts = formatter._timestamp_to_int(event.get("start_time", 0))
        return now_ts - start_ts

    sorted_events = sorted(events, key=lambda e: e.get("start_time", 0))

    target_failure_counts = Counter()
    group_failure_counts = Counter()
    for event in events:
        target_name = event.get("target", "")
        target_failure_counts[target_name] += 1
        event_group = cm.get_target_group(target_name)
        group_failure_counts[event_group] += 1

    worst_targets = target_failure_counts.most_common(5)
    worst_groups = group_failure_counts.most_common(5)

    for event in sorted_events:
        target_name = event.get("target", "")
        start_ts = event.get("start_time", 0)
        start_str = formatter._format_time(start_ts)
        closed = event.get("closed", False)
        alert_count = event.get("alert_count", 1)
        has_critical = event.get("has_critical", False)
        highest_level = "critical" if has_critical else event.get("last_level", "warning")
        recovery_method = event.get("recovery_method", "")
        duration = _get_real_duration(event)

        status_icon = "🔒" if closed else "🔥"
        level_color = Colors.level_color(highest_level)
        level_icon = formatter._colorize(Colors.status_icon(highest_level), level_color)

        if event.get("closed", False) and event.get("duration_seconds"):
            duration_str = format_duration(event.get("duration_seconds", 0))
        elif not closed:
            duration_str = format_duration(duration) + " (进行中)"
        else:
            duration_str = format_duration(duration)

        recovery_method_str = ""
        if recovery_method == "auto":
            recovery_method_str = "自动恢复"
        elif recovery_method == "manual":
            recovery_method_str = f"手动处理 ({event.get('close_handler', '')})"

        is_new_round = event.get("is_new_round", False)
        new_round_str = formatter._colorize(" [新轮次]", Colors.MAGENTA) if is_new_round else ""

        first_msg = event.get("first_message", "")
        last_msg = event.get("last_message", "")

        line = (f"{status_icon} [{start_str}] {formatter._colorize(target_name.ljust(15), Colors.BOLD)} "
                f"{level_icon} {alert_count:>3}次告警 | 最高{highest_level.upper():>8} | "
                f"持续 {duration_str:<18}{new_round_str}")
        if closed and recovery_method_str:
            line += f" | {formatter._colorize(recovery_method_str, Colors.GREEN if recovery_method == 'auto' else Colors.CYAN)}"

        click.echo(line)

        if verbose and not quiet:
            if first_msg != last_msg:
                click.echo(f"     首次: {first_msg}")
                click.echo(f"     最后: {last_msg}")
            else:
                click.echo(f"     {first_msg}")
            event_id = event.get("id", "")[:8]
            level_change = f"{event.get('first_level', '').upper()} -> {event.get('last_level', '').upper()}"
            event_group = cm.get_target_group(target_name)
            click.echo(f"     级别变化: {level_change} | 服务组: {event_group} | 事件ID: {event_id}...")
            click.echo("")

    if not quiet:
        click.echo("-" * 110)

        by_duration = sorted(events, key=_get_real_duration, reverse=True)
        by_count = sorted(events, key=lambda e: e.get("alert_count", 0), reverse=True)
        active_events = [e for e in events if not e.get("closed", False)]

        click.echo("")
        click.echo(formatter._colorize("📊 故障排行:", Colors.BOLD + Colors.MAGENTA))

        if by_duration:
            longest_target = by_duration[0].get("target", "")
            longest_duration = _get_real_duration(by_duration[0])
            click.echo(f"  最长故障: {longest_target} - {format_duration(longest_duration)}")

        if by_count and by_count[0].get("alert_count", 0) > 1:
            click.echo(f"  最多告警: {by_count[0].get('target', '')} - {by_count[0].get('alert_count', 0)} 次")

        if active_events:
            click.echo(formatter._colorize(f"  进行中事件: {len(active_events)} 个", Colors.RED))
            for ae in active_events[:5]:
                ae_start = formatter._format_time(ae.get("start_time", 0))
                ae_duration = format_duration(_get_real_duration(ae))
                ae_level = "CRITICAL" if ae.get("has_critical", False) else ae.get("last_level", "WARNING").upper()
                click.echo(f"    - {ae.get('target', '')} ({ae_level}, 已持续 {ae_duration}, 开始于 {ae_start})")

        if worst_targets and len(worst_targets) > 1 or (worst_targets and worst_targets[0][1] > 1):
            click.echo("")
            click.echo("  故障次数最多的目标:")
            for i, (t, c) in enumerate(worst_targets[:5], 1):
                rank_color = Colors.RED if i <= 3 else Colors.YELLOW
                click.echo(f"    {formatter._colorize(f'#{i}', rank_color)} {t.ljust(16)} {c} 次")

        if worst_groups and (len(worst_groups) > 1 or (worst_groups and worst_groups[0][0] != "default")):
            click.echo("")
            click.echo("  故障次数最多的服务组:")
            for i, (g, c) in enumerate(worst_groups[:5], 1):
                if g == "default" and c == 0:
                    continue
                rank_color = Colors.RED if i <= 3 else Colors.YELLOW
                click.echo(f"    {formatter._colorize(f'#{i}', rank_color)} {g.ljust(16)} {c} 次")

    if quiet:
        total_events = len(events)
        closed_events = sum(1 for e in events if e.get("closed", False))
        active_events = total_events - closed_events
        has_critical_count = sum(1 for e in events if e.get("has_critical", False))
        total_alerts = sum(e.get("alert_count", 0) for e in events)
        worst_target = worst_targets[0][0] if worst_targets else "-"
        worst_count = worst_targets[0][1] if worst_targets else 0
        click.echo(formatter._colorize(
            f"共 {total_events} 个事件, 进行中 {active_events}, 已结束 {closed_events}, "
            f"含严重 {has_critical_count}, 累计告警 {total_alerts} 次 | "
            f"故障最多: {worst_target} ({worst_count}次)",
            "\033[93m"
        ))


@report.command("handover")
@click.option("--hours", type=int, default=8, help="统计时长（小时，默认8小时值班周期）")
@click.option("--config-dir", type=click.Path(), help="配置目录路径")
@click.pass_context
def handover(ctx, hours, config_dir):
    """值班交接摘要"""
    from pathlib import Path
    from collections import Counter
    cm = ConfigManager(Path(config_dir)) if config_dir else ConfigManager()

    verbose = ctx.obj.get("verbose", False)
    quiet = ctx.obj.get("quiet", False)
    formatter = OutputFormatter(verbose=verbose, quiet=quiet)

    if hours <= 0:
        raise ValidationError("统计时长必须大于 0 小时")

    end_time = datetime.now()
    start_time = end_time - timedelta(hours=hours)
    now_ts = int(end_time.timestamp())

    config = cm.load_config()
    all_alerts = cm.get_alerts()
    all_events = cm.get_events(
        start_time=start_time,
        end_time=end_time,
        limit=100,
        impact_window=True
    )
    all_active_events = cm.get_events(only_active=True, limit=100)

    def _event_priority_key(event):
        start_ts = formatter._timestamp_to_int(event.get("start_time", 0))
        duration = now_ts - start_ts
        level_rank = 0 if event.get("has_critical", False) else 1
        count = event.get("alert_count", 1)
        return (-duration, level_rank, -count)

    all_active_events.sort(key=_event_priority_key)

    unhandled_alerts = [a for a in all_alerts if not a.get("handled", False)]
    active_events = all_active_events
    recently_recovered = [e for e in all_events if e.get("closed", False) and e.get("recovery_time") and e.get("recovery_time", 0) >= int(start_time.timestamp())]

    target_failure_counts = Counter()
    group_failure_counts = Counter()
    for event in all_events:
        target = event.get("target", "")
        target_failure_counts[target] += 1
        group = cm.get_target_group(target)
        group_failure_counts[group] += 1

    worst_targets = target_failure_counts.most_common(5)
    worst_groups = group_failure_counts.most_common(5)

    period_str = f"{start_time.strftime('%Y-%m-%d %H:%M:%S')} 至 {end_time.strftime('%Y-%m-%d %H:%M:%S')}"

    def _get_alert_for_event(event):
        event_id = event.get("id", "")
        for a in all_alerts:
            if a.get("event_id") == event_id and not a.get("handled", False):
                return a
        return None

    def _get_target_last_alert(target):
        target_alerts = [a for a in all_alerts if a.get("target") == target]
        target_alerts.sort(key=lambda x: formatter._timestamp_to_int(x.get("last_timestamp", x.get("timestamp", 0))), reverse=True)
        return target_alerts[0] if target_alerts else None

    if quiet:
        worst_active = active_events[0] if active_events else None
        worst_active_str = ""
        if worst_active:
            worst_target = worst_active.get("target", "")
            worst_level = "CRITICAL" if worst_active.get("has_critical", False) else worst_active.get("last_level", "WARNING").upper()
            start_ts = formatter._timestamp_to_int(worst_active.get("start_time", 0))
            worst_duration = format_duration(now_ts - start_ts)
            worst_active_str = f" | 最严重: {worst_target} ({worst_level}, 已持续{worst_duration})"

        output = [
            formatter._colorize(f"交接班: {period_str} ({hours}小时)", Colors.BOLD + Colors.BLUE),
            f"未处理告警: {len(unhandled_alerts)} | 进行中事件: {len(active_events)} | 最近恢复: {len(recently_recovered)}{worst_active_str}"
        ]
        if worst_targets and worst_targets[0][1] > 0:
            output.append(f"故障最多: {worst_targets[0][0]} ({worst_targets[0][1]}次)")
        click.echo("\n".join(output))
        return

    output = []

    output.append("")
    output.append("=" * 90)
    output.append(formatter._colorize(f"📋 值班交接摘要 - 风险总览", Colors.BOLD + Colors.MAGENTA))
    output.append(f"   统计周期: {period_str} ({hours}小时)")
    output.append("=" * 90)

    output.append("")
    output.append(formatter._colorize(f"� 进行中事件 - 按风险排序 ({len(active_events)} 个):", Colors.BOLD + Colors.RED))
    if active_events:
        for event in active_events[:10]:
            target = event.get("target", "")
            start_str = formatter._format_time(event.get("start_time", 0))
            count = event.get("alert_count", 1)
            has_critical = event.get("has_critical", False)
            level = "CRITICAL" if has_critical else event.get("last_level", "WARNING").upper()
            level_color = Colors.RED if has_critical else Colors.YELLOW
            level_str = formatter._colorize(level, level_color)
            start_ts_int = formatter._timestamp_to_int(event.get("start_time", 0))
            duration_sec = now_ts - start_ts_int
            duration = format_duration(duration_sec)
            duration_warn = " ⚠️" if duration_sec > 3600 else ""
            group = cm.get_target_group(target)
            last_alert = _get_target_last_alert(target)
            last_msg = last_alert.get("last_message", last_alert.get("message", "")) if last_alert else event.get("last_message", "")
            last_update = formatter._format_time(event.get("last_update", 0))
            is_new_round = event.get("is_new_round", False)
            new_round_str = formatter._colorize(" [新轮次]", Colors.MAGENTA) if is_new_round else ""

            output.append(f"   {'─'*86}")
            output.append(f"   {formatter._colorize('●', level_color)} {formatter._colorize(target.ljust(18), Colors.BOLD)} {level_str:>10} | 服务组: {group}{new_round_str}")
            output.append(f"      开始时间: {start_str} | 已持续: {formatter._colorize(duration, level_color)}{duration_warn} | 累计告警: {count} 次")
            output.append(f"      最后更新: {last_update}")
            output.append(f"      最后告警: {last_msg}")
            if event.get("timeline") and len(event.get("timeline", [])) > 1:
                timeline = event.get("timeline", [])
                last_update_entry = timeline[-1] if timeline else None
                if last_update_entry and last_update_entry.get("type") in ["level_change", "update"]:
                    rt = last_update_entry.get("response_time", 0)
                    entry_level = last_update_entry.get("level", "").upper()
                    output.append(f"      最近状态: {entry_level} - 响应 {rt:.0f}ms")

            notes = [e for e in event.get("timeline", []) if e.get("type") == "note"]
            if notes:
                latest_note = notes[-1]
                note_author = latest_note.get("author", "")
                note_text = latest_note.get("note", "")
                note_category = latest_note.get("category", "")
                author_str = f" ({note_author})" if note_author else ""
                category_str = f" [{note_category}]" if note_category else ""
                output.append(f"      最新备注{category_str}{author_str}: {note_text}")
    else:
        output.append(formatter._colorize("   ✅ 无进行中事件", Colors.GREEN))

    output.append("")
    output.append(formatter._colorize(f"🔴 未处理告警 ({len(unhandled_alerts)} 条):", Colors.BOLD + Colors.RED))
    if unhandled_alerts:
        for alert in unhandled_alerts[:10]:
            output.append(f"   {formatter.format_alert(alert)}")
    else:
        output.append(formatter._colorize("   ✅ 无未处理告警", Colors.GREEN))

    output.append("")
    output.append(formatter._colorize(f"✅ 最近恢复事件 ({len(recently_recovered)} 个):", Colors.BOLD + Colors.GREEN))
    if recently_recovered:
        for event in recently_recovered[:10]:
            target = event.get("target", "")
            recovery_ts = event.get("recovery_time", 0)
            recovery_str = formatter._format_time(recovery_ts)
            duration = event.get("duration_seconds", 0)
            duration_str = format_duration(duration) if duration else "未知"
            method = event.get("recovery_method", "")
            method_str = "自动恢复" if method == "auto" else f"手动处理 ({event.get('close_handler', '')})"
            output.append(f"   ↻ {target.ljust(15)} 恢复于 {recovery_str} | 持续 {duration_str:>12} | {method_str}")
    else:
        output.append(formatter._colorize("   无最近恢复事件", Colors.GRAY))

    output.append("")
    output.append(formatter._colorize("📊 故障统计:", Colors.BOLD + Colors.MAGENTA))

    if worst_targets:
        output.append("")
        output.append("   故障次数最多的目标:")
        for i, (target, count) in enumerate(worst_targets, 1):
            rank_color = Colors.RED if i <= 3 else (Colors.YELLOW if i <= 5 else Colors.RESET)
            rank_str = formatter._colorize(f"#{i}", rank_color)
            output.append(f"      {rank_str} {target.ljust(18)} {count} 次故障")

    if worst_groups and len(worst_groups) > 1 or (worst_groups and worst_groups[0][0] != "default"):
        output.append("")
        output.append("   故障次数最多的服务组:")
        for i, (group, count) in enumerate(worst_groups, 1):
            if group == "default" and count == 0:
                continue
            rank_color = Colors.RED if i <= 3 else (Colors.YELLOW if i <= 5 else Colors.RESET)
            rank_str = formatter._colorize(f"#{i}", rank_color)
            output.append(f"      {rank_str} {group.ljust(18)} {count} 次故障")

    if verbose:
        output.append("")
        output.append(formatter._colorize("📋 值班期间完整事件列表:", Colors.BOLD + Colors.CYAN))
        for event in sorted(all_events, key=lambda e: e.get("start_time", 0), reverse=True):
            output.append(f"   {formatter.format_event(event)}")

    output.append("")
    output.append("=" * 90)
    output.append(formatter._colorize(f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", Colors.GRAY))
    output.append("=" * 90)

    click.echo("\n".join(output))


@report.command("sla")
@click.option("--hours", type=int, default=24, help="统计时长（小时，默认24小时）")
@click.option("--group", help="按服务组筛选")
@click.option("--output", "-o", type=click.Path(), help="导出到文件")
@click.option("--format", "fmt", type=click.Choice(["json", "csv"]), default="json", help="导出格式")
@click.option("--config-dir", type=click.Path(), help="配置目录路径")
@click.pass_context
def sla(ctx, hours, group, output, fmt, config_dir):
    """可靠性摘要 - 统计可用率、故障时长、MTTR、MTBF"""
    import json
    import csv
    from io import StringIO
    from pathlib import Path
    cm = ConfigManager(Path(config_dir)) if config_dir else ConfigManager()

    verbose = ctx.obj.get("verbose", False)
    quiet = ctx.obj.get("quiet", False)
    formatter = OutputFormatter(verbose=verbose, quiet=quiet)

    if hours <= 0:
        raise ValidationError("统计时长必须大于 0 小时")

    end_time = datetime.now()
    start_time = end_time - timedelta(hours=hours)
    now_ts = int(end_time.timestamp())
    start_ts = int(start_time.timestamp())
    total_duration = now_ts - start_ts

    all_targets = cm.load_config()["targets"]
    if group:
        target_names = [t for t, cfg in all_targets.items() if cfg.get("group", "default") == group]
    else:
        target_names = list(all_targets.keys())

    events = cm.get_events(
        group=group,
        start_time=start_time,
        end_time=end_time,
        limit=500,
        impact_window=True
    )

    def _get_event_outage_duration(event):
        event_start = formatter._timestamp_to_int(event.get("start_time", 0))
        if event.get("closed", False) and event.get("recovery_time"):
            event_end = formatter._timestamp_to_int(event["recovery_time"])
        elif not event.get("closed", False):
            event_end = now_ts
        else:
            event_end = formatter._timestamp_to_int(event.get("close_time", now_ts))
        overlap_start = max(event_start, start_ts)
        overlap_end = min(event_end, now_ts)
        return max(0, overlap_end - overlap_start)

    target_stats = {}
    group_stats = {}

    for target_name in target_names:
        target_cfg = all_targets.get(target_name, {})
        target_group = target_cfg.get("group", "default")
        target_events = [e for e in events if e.get("target") == target_name]

        total_outage = sum(_get_event_outage_duration(e) for e in target_events)
        longest_outage = max(
            (_get_event_outage_duration(e) for e in target_events),
            default=0
        )

        recovered_events = [e for e in target_events if e.get("closed", False) and e.get("recovery_time")]
        total_recovery_time = sum(e.get("duration_seconds", 0) for e in recovered_events)
        mttr = total_recovery_time / len(recovered_events) if recovered_events else 0
        num_failures = len(target_events)
        uptime = total_duration - total_outage
        availability = (uptime / total_duration * 100) if total_duration > 0 else 100.0
        mtbf = (uptime / num_failures) if num_failures > 0 and uptime > 0 else 0

        target_stats[target_name] = {
            "target": target_name,
            "group": target_group,
            "total_duration_seconds": total_duration,
            "total_outage_seconds": total_outage,
            "longest_outage_seconds": longest_outage,
            "uptime_seconds": uptime,
            "availability_pct": round(availability, 4),
            "num_failures": num_failures,
            "num_recovered": len(recovered_events),
            "mttr_seconds": round(mttr, 2),
            "mtbf_seconds": round(mtbf, 2)
        }

        if target_group not in group_stats:
            group_stats[target_group] = {
                "group": target_group,
                "total_duration_seconds": total_duration,
                "total_outage_seconds": 0,
                "longest_outage_seconds": 0,
                "uptime_seconds": 0,
                "availability_pct": 0,
                "num_targets": 0,
                "total_failures": 0,
                "total_recovered": 0,
                "total_mttr_seconds": 0,
                "num_mttr_events": 0,
                "mttr_seconds": 0,
                "mtbf_seconds": 0
            }
        gs = group_stats[target_group]
        gs["num_targets"] += 1
        gs["total_outage_seconds"] += total_outage
        gs["longest_outage_seconds"] = max(gs["longest_outage_seconds"], longest_outage)
        gs["total_failures"] += num_failures
        gs["total_recovered"] += len(recovered_events)
        gs["total_mttr_seconds"] += total_recovery_time
        gs["num_mttr_events"] += len(recovered_events)

    for gname, gs in group_stats.items():
        gs["uptime_seconds"] = gs["total_duration_seconds"] * gs["num_targets"] - gs["total_outage_seconds"]
        total_uptime_group = gs["total_duration_seconds"] * gs["num_targets"]
        gs["availability_pct"] = round(
            (gs["uptime_seconds"] / total_uptime_group * 100) if total_uptime_group > 0 else 100.0,
            4
        )
        gs["mttr_seconds"] = round(
            gs["total_mttr_seconds"] / gs["num_mttr_events"] if gs["num_mttr_events"] > 0 else 0,
            2
        )
        group_total_uptime = gs["uptime_seconds"]
        gs["mtbf_seconds"] = round(
            (group_total_uptime / gs["total_failures"]) if gs["total_failures"] > 0 and group_total_uptime > 0 else 0,
            2
        )

    if output:
        export_data = {
            "period": {
                "start_time": start_time.strftime("%Y-%m-%d %H:%M:%S"),
                "end_time": end_time.strftime("%Y-%m-%d %H:%M:%S"),
                "hours": hours,
                "total_duration_seconds": total_duration
            },
            "targets": target_stats,
            "groups": group_stats
        }

        if fmt == "json":
            content = json.dumps(export_data, indent=2, ensure_ascii=False)
        else:
            output_io = StringIO()
            writer = csv.writer(output_io)
            writer.writerow([
                "类型", "名称", "服务组", "可用率(%)", "总时长(秒)", "故障总时长(秒)",
                "最长故障(秒)", "故障次数", "恢复次数", "MTTR(秒)", "MTBF(秒)"
            ])
            for tname, ts in target_stats.items():
                writer.writerow([
                    "目标", tname, ts["group"],
                    f"{ts['availability_pct']:.4f}",
                    ts["total_duration_seconds"],
                    ts["total_outage_seconds"],
                    ts["longest_outage_seconds"],
                    ts["num_failures"],
                    ts["num_recovered"],
                    ts["mttr_seconds"],
                    ts["mtbf_seconds"]
                ])
            for gname, gs in group_stats.items():
                writer.writerow([
                    "服务组", gname, gname,
                    f"{gs['availability_pct']:.4f}",
                    gs["total_duration_seconds"] * gs["num_targets"],
                    gs["total_outage_seconds"],
                    gs["longest_outage_seconds"],
                    gs["total_failures"],
                    gs["total_recovered"],
                    gs["mttr_seconds"],
                    gs["mtbf_seconds"]
                ])
            content = output_io.getvalue()

        with open(output, "w", encoding="utf-8") as f:
            f.write(content)
        click.echo(formatter._colorize(f"✅ 可靠性数据已导出到 {output} ({fmt} 格式)", "\033[92m"))
        return

    sorted_targets = sorted(
        target_stats.values(),
        key=lambda x: (x["availability_pct"], -x["total_outage_seconds"])
    )

    if quiet:
        output_lines = [
            formatter._colorize(f"可靠性摘要 ({hours}小时)", Colors.BOLD + Colors.BLUE),
        ]
        worst = sorted_targets[0] if sorted_targets else None
        if worst:
            output_lines.append(
                f"可用率最低: {worst['target']} ({worst['availability_pct']:.2f}%) | "
                f"故障时长: {format_duration(worst['total_outage_seconds'])}"
            )
        if group_stats:
            worst_group = sorted(group_stats.values(), key=lambda x: x["availability_pct"])[0]
            output_lines.append(
                f"组可用率最低: {worst_group['group']} ({worst_group['availability_pct']:.2f}%)"
            )
        click.echo("\n".join(output_lines))
        return

    output_lines = []
    period_str = f"{start_time.strftime('%Y-%m-%d %H:%M:%S')} 至 {end_time.strftime('%Y-%m-%d %H:%M:%S')}"

    output_lines.append("")
    output_lines.append("=" * 100)
    output_lines.append(formatter._colorize(f"📊 可靠性摘要", Colors.BOLD + Colors.MAGENTA))
    output_lines.append(f"   统计周期: {period_str} ({hours}小时)")
    output_lines.append("=" * 100)

    def _format_stat_row(name, avail, total_outage, longest_outage, num_failures, mttr, mtbf, indent="   "):
        avail_color = Colors.GREEN if avail >= 99.9 else (Colors.YELLOW if avail >= 99 else Colors.RED)
        avail_str = formatter._colorize(f"{avail:.4f}%", avail_color)

        parts = [
            f"{indent}{name.ljust(18)}",
            f"可用率: {avail_str}",
            f"故障总时长: {format_duration(total_outage):>12}",
            f"最长故障: {format_duration(longest_outage):>12}",
            f"故障次数: {num_failures:>3}"
        ]
        if mttr > 0:
            parts.append(f"MTTR: {format_duration(mttr):>12}")
        if mtbf > 0:
            parts.append(f"MTBF: {format_duration(mtbf):>12}")
        return " | ".join(parts)

    output_lines.append("")
    output_lines.append(formatter._colorize("🎯 按目标统计:", Colors.BOLD + Colors.CYAN))
    display_count = 3 if not verbose else len(sorted_targets)
    for ts in sorted_targets[:display_count]:
        output_lines.append(_format_stat_row(
            ts["target"],
            ts["availability_pct"],
            ts["total_outage_seconds"],
            ts["longest_outage_seconds"],
            ts["num_failures"],
            ts["mttr_seconds"],
            ts["mtbf_seconds"]
        ))
    if not verbose and len(sorted_targets) > display_count:
        remaining = len(sorted_targets) - display_count
        output_lines.append(f"   ... 还有 {remaining} 个目标, 使用 -v 查看全部")

    if verbose:
        output_lines.append("")
        output_lines.append(formatter._colorize("📦 按服务组统计:", Colors.BOLD + Colors.CYAN))
        sorted_groups = sorted(group_stats.values(), key=lambda x: x["availability_pct"])
        for gs in sorted_groups:
            output_lines.append(_format_stat_row(
                gs["group"],
                gs["availability_pct"],
                gs["total_outage_seconds"],
                gs["longest_outage_seconds"],
                gs["total_failures"],
                gs["mttr_seconds"],
                gs["mtbf_seconds"],
                indent="   "
            ))

    output_lines.append("")
    output_lines.append("=" * 100)
    output_lines.append(formatter._colorize(
        f"可用率: 正常≥99.9%(绿) | 关注99-99.9%(黄) | 异常<99%(红)",
        Colors.GRAY
    ))
    output_lines.append(formatter._colorize(
        f"MTTR=平均恢复时间 | MTBF=平均无故障时间",
        Colors.GRAY
    ))
    output_lines.append("=" * 100)

    click.echo("\n".join(output_lines))
