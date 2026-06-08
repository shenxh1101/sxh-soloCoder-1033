import json
import csv
from datetime import datetime, timedelta
from io import StringIO
import click
from opsmonitor.config import ConfigManager, ValidationError
from opsmonitor.formatter import OutputFormatter, percentile, p95, p99, format_duration


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
            rt = entry.get("response_time_ms")
            if rt is not None:
                success_times.append(rt)
                if min_time is None or rt < min_time:
                    min_time = rt
                if max_time is None or rt > max_time:
                    max_time = rt
        else:
            failed_count += 1
            rt = entry.get("response_time_ms")
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
        "active_events_count": sum(1 for e in all_events if e.get("active")),
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
@click.option("--hours", type=int, default=24, help="统计时长（小时）")
@click.option("--config-dir", type=click.Path(), help="配置目录路径")
@click.pass_context
def timeline(ctx, target, hours, config_dir):
    """生成故障时间线"""
    from pathlib import Path
    cm = ConfigManager(Path(config_dir)) if config_dir else ConfigManager()

    verbose = ctx.obj.get("verbose", False)
    quiet = ctx.obj.get("quiet", False)
    formatter = OutputFormatter(verbose=verbose, quiet=quiet)

    if hours <= 0:
        raise ValidationError("统计时长必须大于 0 小时")

    end_time = datetime.now()
    start_time = end_time - timedelta(hours=hours)

    alerts = cm.get_alerts(target_name=target)
    filtered = []
    for a in alerts:
        ts = _parse_timestamp(a.get("timestamp"))
        if ts and start_time <= ts <= end_time:
            filtered.append(a)

    if not filtered:
        click.echo(formatter._colorize("✅ 该时间段内无告警", "\033[92m"))
        return

    if not quiet:
        click.echo(formatter._colorize(f"⏱️  故障时间线 ({hours}小时内):", "\033[94m"))

    sorted_alerts = sorted(filtered, key=lambda a: a["timestamp"])

    for alert in sorted_alerts:
        level = alert.get("level", "warning")
        status_color = "\033[91m" if level == "critical" else "\033[93m"
        handled = alert.get("handled", False)
        handled_marker = "[已处理]" if handled else "[未处理]"

        line = f"[{alert['timestamp']}] {formatter._colorize(level.upper(), status_color)} {alert['target']}: {alert['message']}"
        if verbose and not quiet:
            line += f" {formatter._colorize(handled_marker, '\033[90m')}"
        click.echo(line)

    if quiet:
        critical_count = sum(1 for a in sorted_alerts if a.get("level") == "critical")
        warning_count = sum(1 for a in sorted_alerts if a.get("level") == "warning")
        handled_count = sum(1 for a in sorted_alerts if a.get("handled"))
        click.echo(formatter._colorize(
            f"共 {len(sorted_alerts)} 条告警, 严重 {critical_count}, 警告 {warning_count}, 已处理 {handled_count}",
            "\033[93m"
        ))
