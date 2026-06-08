import click
from opsmonitor.config import ConfigManager
from opsmonitor.formatter import OutputFormatter


@click.group()
def alert():
    """告警管理：阈值设置、静音、查看、处理"""
    pass


@alert.command("set-threshold")
@click.option("--warning", type=int, help="警告响应时间阈值（毫秒）")
@click.option("--critical", type=int, help="严重响应时间阈值（毫秒）")
@click.option("--failures", type=int, help="连续失败次数阈值")
@click.option("--config-dir", type=click.Path(), help="配置目录路径")
@click.pass_context
def set_threshold(ctx, warning, critical, failures, config_dir):
    """设置告警阈值"""
    from pathlib import Path
    cm = ConfigManager(Path(config_dir)) if config_dir else ConfigManager()
    verbose = ctx.obj.get("verbose", False)
    formatter = OutputFormatter(verbose=verbose)

    kwargs = {}
    if warning is not None:
        kwargs["response_time_warning"] = warning
    if critical is not None:
        kwargs["response_time_critical"] = critical
    if failures is not None:
        kwargs["consecutive_failures"] = failures

    if not kwargs:
        click.echo(formatter._colorize("⚠️  请至少指定一个阈值参数", "\033[93m"))
        return

    cm.update_thresholds(**kwargs)
    click.echo(formatter._colorize("✅ 阈值已更新", "\033[92m"))

    if verbose:
        config = cm.load_config()
        click.echo("\n当前阈值:")
        for k, v in config["thresholds"].items():
            click.echo(f"  {k}: {v}")


@alert.command("show-thresholds")
@click.option("--config-dir", type=click.Path(), help="配置目录路径")
@click.pass_context
def show_thresholds(ctx, config_dir):
    """显示当前阈值配置"""
    from pathlib import Path
    cm = ConfigManager(Path(config_dir)) if config_dir else ConfigManager()
    verbose = ctx.obj.get("verbose", False)
    formatter = OutputFormatter(verbose=verbose)

    config = cm.load_config()
    thresholds = config["thresholds"]

    click.echo(formatter._colorize("📏 当前告警阈值:", "\033[96m"))
    click.echo(f"  响应时间警告: {thresholds['response_time_warning']}ms")
    click.echo(f"  响应时间严重: {thresholds['response_time_critical']}ms")
    click.echo(f"  连续失败次数: {thresholds['consecutive_failures']} 次")


@alert.command("mute")
@click.argument("target")
@click.option("--duration", type=int, default=60, help="静音时长（分钟），默认60分钟")
@click.option("--reason", default="", help="静音原因")
@click.option("--config-dir", type=click.Path(), help="配置目录路径")
@click.pass_context
def mute(ctx, target, duration, reason, config_dir):
    """静音某个目标的告警"""
    from pathlib import Path
    cm = ConfigManager(Path(config_dir)) if config_dir else ConfigManager()
    verbose = ctx.obj.get("verbose", False)
    formatter = OutputFormatter(verbose=verbose)

    success = cm.mute_target(target, duration, reason)
    if success:
        click.echo(formatter._colorize(f"🔇 目标 '{target}' 已静音 {duration} 分钟", "\033[92m"))
    else:
        click.echo(formatter._colorize(f"❌ 目标 '{target}' 不存在", "\033[91m"))


@alert.command("unmute")
@click.argument("target")
@click.option("--config-dir", type=click.Path(), help="配置目录路径")
@click.pass_context
def unmute(ctx, target, config_dir):
    """取消目标静音"""
    from pathlib import Path
    cm = ConfigManager(Path(config_dir)) if config_dir else ConfigManager()
    verbose = ctx.obj.get("verbose", False)
    formatter = OutputFormatter(verbose=verbose)

    success = cm.unmute_target(target)
    if success:
        click.echo(formatter._colorize(f"🔊 目标 '{target}' 已取消静音", "\033[92m"))
    else:
        click.echo(formatter._colorize(f"⚠️  目标 '{target}' 未被静音", "\033[93m"))


@alert.command("list-muted")
@click.option("--config-dir", type=click.Path(), help="配置目录路径")
@click.pass_context
def list_muted(ctx, config_dir):
    """列出所有静音的目标"""
    from pathlib import Path
    cm = ConfigManager(Path(config_dir)) if config_dir else ConfigManager()
    verbose = ctx.obj.get("verbose", False)
    formatter = OutputFormatter(verbose=verbose)

    config = cm.load_config()
    muted = config.get("muted", {})

    if not muted:
        click.echo(formatter._colorize("✅ 暂无静音目标", "\033[92m"))
        return

    click.echo(formatter._colorize(f"🔇 当前静音目标 ({len(muted)} 个):", "\033[96m"))
    for name, info in muted.items():
        if cm.is_muted(name):
            click.echo(formatter.format_muted_target(name, info))


@alert.command("list")
@click.option("--target", help="按目标筛选")
@click.option("--unhandled-only", is_flag=True, help="只显示未处理的告警")
@click.option("--limit", type=int, default=50, help="显示数量限制")
@click.option("--config-dir", type=click.Path(), help="配置目录路径")
@click.pass_context
def list_alerts(ctx, target, unhandled_only, limit, config_dir):
    """查看告警列表"""
    from pathlib import Path
    cm = ConfigManager(Path(config_dir)) if config_dir else ConfigManager()
    verbose = ctx.obj.get("verbose", False)
    formatter = OutputFormatter(verbose=verbose)

    alerts = cm.get_alerts(target_name=target, only_unhandled=unhandled_only)
    alerts = alerts[-limit:] if limit else alerts

    if not alerts:
        msg = "✅ 暂无告警" if not unhandled_only else "✅ 暂无未处理告警"
        click.echo(formatter._colorize(msg, "\033[92m"))
        return

    unhandled_count = sum(1 for a in alerts if not a.get("handled", False))
    click.echo(formatter._colorize(f"🚨 告警列表 ({len(alerts)} 条, {unhandled_count} 条未处理):", "\033[96m"))
    click.echo("-" * 80)

    for alert in reversed(alerts):
        click.echo(formatter.format_alert(alert))


@alert.command("handle")
@click.argument("alert_id")
@click.option("--note", default="", help="处理备注")
@click.option("--config-dir", type=click.Path(), help="配置目录路径")
@click.pass_context
def handle_alert(ctx, alert_id, note, config_dir):
    """标记单个告警为已处理"""
    from pathlib import Path
    cm = ConfigManager(Path(config_dir)) if config_dir else ConfigManager()
    verbose = ctx.obj.get("verbose", False)
    formatter = OutputFormatter(verbose=verbose)

    success = cm.mark_alert_handled(alert_id, note)
    if success:
        click.echo(formatter._colorize(f"✅ 告警 {alert_id[:8]}... 已标记为已处理", "\033[92m"))
    else:
        click.echo(formatter._colorize(f"❌ 未找到告警 ID: {alert_id}", "\033[91m"))


@alert.command("handle-target")
@click.argument("target")
@click.option("--note", default="", help="处理备注")
@click.option("--config-dir", type=click.Path(), help="配置目录路径")
@click.pass_context
def handle_target(ctx, target, note, config_dir):
    """标记某个目标的所有告警为已处理"""
    from pathlib import Path
    cm = ConfigManager(Path(config_dir)) if config_dir else ConfigManager()
    verbose = ctx.obj.get("verbose", False)
    formatter = OutputFormatter(verbose=verbose)

    count = cm.mark_target_alerts_handled(target, note)
    if count > 0:
        click.echo(formatter._colorize(f"✅ 目标 '{target}' 的 {count} 条告警已标记为已处理", "\033[92m"))
    else:
        click.echo(formatter._colorize(f"⚠️  目标 '{target}' 没有未处理的告警", "\033[93m"))


@alert.command("anomalies")
@click.option("--target", help="按目标筛选")
@click.option("--limit", type=int, default=20, help="显示数量限制")
@click.option("--config-dir", type=click.Path(), help="配置目录路径")
@click.pass_context
def anomalies(ctx, target, limit, config_dir):
    """查看最近异常记录"""
    from pathlib import Path
    cm = ConfigManager(Path(config_dir)) if config_dir else ConfigManager()
    verbose = ctx.obj.get("verbose", False)
    formatter = OutputFormatter(verbose=verbose)

    history = cm.get_history(target_name=target, limit=limit, only_errors=True)

    if not history:
        click.echo(formatter._colorize("✅ 最近无异常记录", "\033[92m"))
        return

    click.echo(formatter._colorize(f"⚠️  最近异常记录 ({len(history)} 条):", "\033[96m"))
    click.echo("-" * 80)

    for entry in reversed(history):
        ts = formatter._format_time(entry.get("timestamp", 0))
        target_name = entry.get("target", "")
        error = entry.get("error", "Unknown error")
        level = "critical"
        icon = formatter._colorize("✗", "\033[91m")
        click.echo(f"[{ts}] {icon} {target_name}: {error}")
