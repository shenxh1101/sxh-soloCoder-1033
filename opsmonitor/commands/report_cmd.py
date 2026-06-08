import time
import json
import csv
from datetime import datetime, timedelta
from pathlib import Path
import click
from opsmonitor.config import ConfigManager
from opsmonitor.formatter import OutputFormatter


@click.group()
def report():
    """生成值班报告和故障时间线"""
    pass


@report.command("export")
@click.option("--format", "output_format", type=click.Choice(["txt", "json", "csv"]), default="txt", help="输出格式")
@click.option("--output", "-o", type=click.Path(), help="输出文件路径")
@click.option("--hours", type=int, default=24, help="统计最近多少小时的数据")
@click.option("--config-dir", type=click.Path(), help="配置目录路径")
@click.pass_context
def export_report(ctx, output_format, output, hours, config_dir):
    """导出值班报告"""
    from pathlib import Path
    cm = ConfigManager(Path(config_dir)) if config_dir else ConfigManager()
    verbose = ctx.obj.get("verbose", False)
    formatter = OutputFormatter(verbose=verbose)

    config = cm.load_config()
    thresholds = config["thresholds"]

    now = int(time.time())
    start_time = now - hours * 3600

    history = cm.get_history(limit=10000)
    history = [h for h in history if h.get("timestamp", 0) >= start_time]

    alerts = cm.get_alerts()
    alerts = [a for a in alerts if a.get("timestamp", 0) >= start_time]

    targets = config["targets"]
    groups = config["groups"]

    report_data = {
        "report_time": now,
        "start_time": start_time,
        "hours": hours,
        "summary": {},
        "targets": {},
        "alerts": alerts,
        "groups": {}
    }

    total_checks = len(history)
    successful_checks = sum(1 for h in history if h.get("success", False))
    failed_checks = total_checks - successful_checks

    total_alerts = len(alerts)
    unhandled_alerts = sum(1 for a in alerts if not a.get("handled", False))
    critical_alerts = sum(1 for a in alerts if a.get("level") == "critical")
    warning_alerts = sum(1 for a in alerts if a.get("level") == "warning")

    report_data["summary"] = {
        "total_checks": total_checks,
        "successful_checks": successful_checks,
        "failed_checks": failed_checks,
        "success_rate": (successful_checks / total_checks * 100) if total_checks > 0 else 0,
        "total_alerts": total_alerts,
        "unhandled_alerts": unhandled_alerts,
        "critical_alerts": critical_alerts,
        "warning_alerts": warning_alerts,
        "targets_count": len(targets),
        "groups_count": len(groups)
    }

    for target_name, target_config in targets.items():
        target_history = [h for h in history if h.get("target") == target_name]
        target_alerts = [a for a in alerts if a.get("target") == target_name]

        if target_history:
            response_times = [h.get("response_time", 0) for h in target_history if h.get("success", False)]
            avg_rt = sum(response_times) / len(response_times) if response_times else 0
            max_rt = max(response_times) if response_times else 0
            min_rt = min(response_times) if response_times else 0
            success_count = sum(1 for h in target_history if h.get("success", False))
            success_rate = success_count / len(target_history) * 100
        else:
            avg_rt = max_rt = min_rt = 0
            success_count = 0
            success_rate = 0

        report_data["targets"][target_name] = {
            "config": target_config,
            "total_checks": len(target_history),
            "successful_checks": success_count,
            "success_rate": success_rate,
            "avg_response_time": avg_rt,
            "max_response_time": max_rt,
            "min_response_time": min_rt,
            "alerts_count": len(target_alerts),
            "unhandled_alerts": sum(1 for a in target_alerts if not a.get("handled", False)),
            "muted": cm.is_muted(target_name)
        }

    for group_name, target_names in groups.items():
        group_checks = 0
        group_success = 0
        group_alerts = 0

        for target_name in target_names:
            tdata = report_data["targets"].get(target_name, {})
            group_checks += tdata.get("total_checks", 0)
            group_success += tdata.get("successful_checks", 0)
            group_alerts += tdata.get("alerts_count", 0)

        report_data["groups"][group_name] = {
            "targets_count": len(target_names),
            "total_checks": group_checks,
            "successful_checks": group_success,
            "success_rate": (group_success / group_checks * 100) if group_checks > 0 else 0,
            "alerts_count": group_alerts
        }

    if output_format == "json":
        content = json.dumps(report_data, indent=2, ensure_ascii=False)
    elif output_format == "csv":
        content = _generate_csv_report(report_data, formatter)
    else:
        content = _generate_text_report(report_data, formatter, verbose)

    if output:
        output_path = Path(output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(content)
        click.echo(formatter._colorize(f"✅ 报告已导出到: {output_path}", "\033[92m"))
    else:
        click.echo(content)


def _generate_text_report(data, formatter, verbose):
    lines = []
    lines.append("=" * 70)
    lines.append(formatter._colorize("📋 运维监控值班报告", "\033[96m" + "\033[1m"))
    lines.append("=" * 70)
    lines.append(f"统计时段: {formatter._format_time(data['start_time'])} - {formatter._format_time(data['report_time'])}")
    lines.append(f"统计时长: {data['hours']} 小时")
    lines.append(f"生成时间: {formatter._format_time(data['report_time'])}")

    s = data["summary"]
    lines.append("")
    lines.append(formatter.format_report_section("总体概览",
        f"  监控目标数: {s['targets_count']} 个 ({s['groups_count']} 个服务组)\n"
        f"  总检查次数: {s['total_checks']} 次\n"
        f"  成功次数: {s['successful_checks']} 次 | 失败次数: {s['failed_checks']} 次\n"
        f"  成功率: {s['success_rate']:.2f}%\n"
        f"  告警总数: {s['total_alerts']} 条\n"
        f"  - 严重告警: {s['critical_alerts']} 条\n"
        f"  - 警告告警: {s['warning_alerts']} 条\n"
        f"  未处理: {s['unhandled_alerts']} 条"
    ))

    lines.append(formatter.format_report_section("服务组统计", ""))
    for group_name, gdata in data["groups"].items():
        status_color = "\033[92m" if gdata["success_rate"] >= 95 else ("\033[93m" if gdata["success_rate"] >= 80 else "\033[91m")
        success_rate_str = f"{gdata['success_rate']:.1f}%"
        lines.append(f"  {formatter._colorize(group_name.ljust(20), '\033[1m')} "
                     f"目标: {str(gdata['targets_count']).ljust(3)} | "
                     f"检查: {str(gdata['total_checks']).ljust(6)} | "
                     f"成功率: {formatter._colorize(success_rate_str, status_color)} | "
                     f"告警: {gdata['alerts_count']} 条")

    lines.append(formatter.format_report_section("目标详情", ""))
    for target_name, tdata in sorted(data["targets"].items()):
        muted = tdata["muted"]
        status = "🔇" if muted else ("✅" if tdata["success_rate"] >= 95 else ("⚠️ " if tdata["success_rate"] >= 80 else "❌"))
        status_color = "\033[92m" if tdata["success_rate"] >= 95 else ("\033[93m" if tdata["success_rate"] >= 80 else "\033[91m")
        lines.append(f"  {status} {formatter._colorize(target_name.ljust(18), '\033[1m')} "
                     f"[{tdata['config']['type']}] {tdata['config']['address']}")
        t_success_rate = f"{tdata['success_rate']:.1f}%"
        lines.append(f"     检查: {tdata['total_checks']} 次 | "
                     f"成功率: {formatter._colorize(t_success_rate, status_color)} | "
                     f"告警: {tdata['alerts_count']} 条")
        if tdata["total_checks"] > 0:
            lines.append(f"     平均响应: {tdata['avg_response_time']:.0f}ms | "
                         f"最大: {tdata['max_response_time']:.0f}ms | "
                         f"最小: {tdata['min_response_time']:.0f}ms")
        if tdata["unhandled_alerts"] > 0:
            lines.append(formatter._colorize(f"     ⚠️  {tdata['unhandled_alerts']} 条告警未处理", "\033[91m"))
        if muted:
            lines.append(formatter._colorize(f"     🔇 目标已静音", "\033[90m"))
        if verbose:
            lines.append(f"     组: {tdata['config'].get('group', 'default')} | "
                         f"方法: {tdata['config'].get('method', '-')} | "
                         f"期望状态: {tdata['config'].get('expected_status', '-')}")
        lines.append("")

    if data["alerts"]:
        lines.append(formatter.format_report_section("告警记录", ""))
        for alert in sorted(data["alerts"], key=lambda x: x.get("timestamp", 0)):
            lines.append(f"  {formatter.format_alert(alert)}")

    return "\n".join(lines)


def _generate_csv_report(data, formatter):
    import io
    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow(["=== 运维监控值班报告 ==="])
    writer.writerow(["统计时段", f"{formatter._format_time(data['start_time'])} - {formatter._format_time(data['report_time'])}"])
    writer.writerow(["统计时长", f"{data['hours']} 小时"])
    writer.writerow([])

    s = data["summary"]
    writer.writerow(["=== 总体概览 ==="])
    writer.writerow(["监控目标数", s["targets_count"]])
    writer.writerow(["服务组数", s["groups_count"]])
    writer.writerow(["总检查次数", s["total_checks"]])
    writer.writerow(["成功次数", s["successful_checks"]])
    writer.writerow(["失败次数", s["failed_checks"]])
    writer.writerow(["成功率(%)", f"{s['success_rate']:.2f}"])
    writer.writerow(["告警总数", s["total_alerts"]])
    writer.writerow(["严重告警", s["critical_alerts"]])
    writer.writerow(["警告告警", s["warning_alerts"]])
    writer.writerow(["未处理", s["unhandled_alerts"]])
    writer.writerow([])

    writer.writerow(["=== 目标详情 ==="])
    writer.writerow(["目标名称", "类型", "地址", "组", "检查次数", "成功次数",
                     "成功率(%)", "平均响应(ms)", "最大响应(ms)", "最小响应(ms)",
                     "告警数", "未处理告警", "是否静音"])
    for target_name, tdata in data["targets"].items():
        writer.writerow([
            target_name,
            tdata["config"]["type"],
            tdata["config"]["address"],
            tdata["config"].get("group", "default"),
            tdata["total_checks"],
            tdata["successful_checks"],
            f"{tdata['success_rate']:.2f}",
            f"{tdata['avg_response_time']:.0f}",
            f"{tdata['max_response_time']:.0f}",
            f"{tdata['min_response_time']:.0f}",
            tdata["alerts_count"],
            tdata["unhandled_alerts"],
            "是" if tdata["muted"] else "否"
        ])
    writer.writerow([])

    if data["alerts"]:
        writer.writerow(["=== 告警记录 ==="])
        writer.writerow(["时间", "目标", "级别", "类型", "消息", "响应时间(ms)", "连续失败", "是否处理", "处理备注"])
        for alert in data["alerts"]:
            writer.writerow([
                formatter._format_time(alert.get("timestamp", 0)),
                alert.get("target", ""),
                alert.get("level", ""),
                alert.get("type", ""),
                alert.get("message", ""),
                f"{alert.get('response_time', 0):.0f}",
                alert.get("consecutive_failures", 0),
                "是" if alert.get("handled", False) else "否",
                alert.get("handled_note", "")
            ])

    return output.getvalue()


@report.command("timeline")
@click.option("--target", help="按目标筛选")
@click.option("--hours", type=int, default=24, help="最近多少小时")
@click.option("--output", "-o", type=click.Path(), help="输出文件路径")
@click.option("--config-dir", type=click.Path(), help="配置目录路径")
@click.pass_context
def timeline(ctx, target, hours, output, config_dir):
    """生成故障时间线"""
    from pathlib import Path
    cm = ConfigManager(Path(config_dir)) if config_dir else ConfigManager()
    verbose = ctx.obj.get("verbose", False)
    formatter = OutputFormatter(verbose=verbose)

    now = int(time.time())
    start_time = now - hours * 3600

    history = cm.get_history(target_name=target, limit=10000, only_errors=True)
    history = [h for h in history if h.get("timestamp", 0) >= start_time]

    alerts = cm.get_alerts(target_name=target)
    alerts = [a for a in alerts if a.get("timestamp", 0) >= start_time]

    events = []

    for h in history:
        events.append({
            "timestamp": h.get("timestamp", 0),
            "type": "failure",
            "target": h.get("target", ""),
            "level": "critical",
            "message": h.get("error", "检查失败")
        })

    for a in alerts:
        events.append({
            "timestamp": a.get("timestamp", 0),
            "type": "alert",
            "target": a.get("target", ""),
            "level": a.get("level", "warning"),
            "message": f"[ALERT] {a.get('message', '')}"
        })

    events.sort(key=lambda x: x["timestamp"])

    if not events:
        click.echo(formatter._colorize(f"✅ 最近 {hours} 小时无故障事件", "\033[92m"))
        return

    lines = []
    lines.append("=" * 70)
    lines.append(formatter._colorize(f"⏱️  故障时间线 (最近 {hours} 小时)", "\033[96m" + "\033[1m"))
    lines.append("=" * 70)
    lines.append(f"统计时段: {formatter._format_time(start_time)} - {formatter._format_time(now)}")
    lines.append(f"事件总数: {len(events)} 个")
    lines.append("")

    last_time = None
    for event in events:
        current_time = datetime.fromtimestamp(event["timestamp"])
        if last_time:
            gap = current_time - last_time
            if gap.total_seconds() > 300:
                lines.append(formatter._colorize(f"  ... {gap.total_seconds() / 60:.0f} 分钟无事件 ...", "\033[90m"))
        last_time = current_time

        lines.append(formatter.format_timeline_event(event))

    content = "\n".join(lines)

    if output:
        output_path = Path(output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(content)
        click.echo(formatter._colorize(f"✅ 时间线已导出到: {output_path}", "\033[92m"))
    else:
        click.echo(content)
