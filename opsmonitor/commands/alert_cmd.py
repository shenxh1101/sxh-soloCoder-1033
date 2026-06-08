import click
from opsmonitor.config import ConfigManager, ValidationError
from opsmonitor.formatter import OutputFormatter


@click.group()
def alert():
    """告警管理"""
    pass


@alert.command("list")
@click.option("--target", help="按目标名称筛选")
@click.option("--only-unhandled", is_flag=True, help="仅显示未处理告警")
@click.option("--only-muted", is_flag=True, help="仅显示已静音目标的告警")
@click.option("--level", type=click.Choice(["critical", "warning"]), help="按严重级别筛选")
@click.option("--group", help="按服务组筛选")
@click.option("--limit", type=int, default=50, help="显示条数限制")
@click.option("--config-dir", type=click.Path(), help="配置目录路径")
@click.pass_context
def list_alerts(ctx, target, only_unhandled, only_muted, level, group, limit, config_dir):
    """列出告警"""
    from pathlib import Path
    cm = ConfigManager(Path(config_dir)) if config_dir else ConfigManager()

    verbose = ctx.obj.get("verbose", False)
    quiet = ctx.obj.get("quiet", False)
    formatter = OutputFormatter(verbose=verbose, quiet=quiet)

    alerts = cm.get_alerts(
        target_name=target,
        only_unhandled=only_unhandled,
        only_muted=only_muted,
        level=level,
        group=group
    )

    if not alerts:
        click.echo(formatter._colorize("✅ 当前无告警", "\033[92m"))
        return

    filtered = alerts[:limit]

    if not quiet:
        filters = []
        if target:
            filters.append(f"目标={target}")
        if only_unhandled:
            filters.append("仅未处理")
        if only_muted:
            filters.append("仅已静音")
        if level:
            filters.append(f"级别={level}")
        if group:
            filters.append(f"组={group}")
        filter_str = f" (筛选: {', '.join(filters)})" if filters else ""
        click.echo(formatter._colorize(f"📋 告警列表 (共 {len(alerts)} 条{filter_str}):", "\033[94m"))
        if verbose:
            click.echo("-" * 100)

    for alert in filtered:
        click.echo(formatter.format_alert(alert))

    if quiet:
        unhandled = sum(1 for a in alerts if not a.get("handled"))
        critical = sum(1 for a in alerts if a.get("level") == "critical")
        warning = sum(1 for a in alerts if a.get("level") == "warning")
        click.echo(formatter._colorize(
            f"共 {len(alerts)} 条告警, 未处理 {unhandled}, 严重 {critical}, 警告 {warning}",
            "\033[93m"
        ))


@alert.command("handle")
@click.argument("alert_id")
@click.option("--note", help="处理备注")
@click.option("--handler", help="处理人姓名")
@click.option("--conclusion", type=click.Choice(["resolved", "false_alarm", "known_issue", "delegated"]), help="处理结论")
@click.option("--recovery-time", help="恢复时间 (YYYY-MM-DD HH:MM:SS)")
@click.option("--config-dir", type=click.Path(), help="配置目录路径")
@click.pass_context
def handle_alert(ctx, alert_id, note, handler, conclusion, recovery_time, config_dir):
    """标记告警已处理"""
    from pathlib import Path
    cm = ConfigManager(Path(config_dir)) if config_dir else ConfigManager()

    verbose = ctx.obj.get("verbose", False)
    quiet = ctx.obj.get("quiet", False)
    formatter = OutputFormatter(verbose=verbose, quiet=quiet)

    success = cm.mark_alert_handled(
        alert_id=alert_id,
        note=note,
        handler=handler,
        conclusion=conclusion,
        recovery_time=recovery_time
    )

    if success:
        msg = f"✅ 告警 {alert_id} 已标记为已处理"
        if handler and not quiet:
            msg += f"（处理人: {handler}）"
        if conclusion and not quiet:
            msg += f"（结论: {conclusion}）"
        click.echo(formatter._colorize(msg, "\033[92m"))
    else:
        raise ValidationError(f"告警 {alert_id} 不存在")


@alert.command("handle-target")
@click.argument("target_name")
@click.option("--note", help="处理备注")
@click.option("--handler", help="处理人姓名")
@click.option("--conclusion", type=click.Choice(["resolved", "false_alarm", "known_issue", "delegated"]), help="处理结论")
@click.option("--recovery-time", help="恢复时间 (YYYY-MM-DD HH:MM:SS)")
@click.option("--config-dir", type=click.Path(), help="配置目录路径")
@click.pass_context
def handle_target(ctx, target_name, note, handler, conclusion, recovery_time, config_dir):
    """标记目标的所有告警已处理"""
    from pathlib import Path
    cm = ConfigManager(Path(config_dir)) if config_dir else ConfigManager()

    verbose = ctx.obj.get("verbose", False)
    quiet = ctx.obj.get("quiet", False)
    formatter = OutputFormatter(verbose=verbose, quiet=quiet)

    count = cm.mark_target_alerts_handled(
        target_name=target_name,
        note=note,
        handler=handler,
        conclusion=conclusion,
        recovery_time=recovery_time
    )

    if count > 0:
        msg = f"✅ 目标 '{target_name}' 的 {count} 条告警已标记为已处理"
        if handler and not quiet:
            msg += f"（处理人: {handler}）"
        if conclusion and not quiet:
            msg += f"（结论: {conclusion}）"
        click.echo(formatter._colorize(msg, "\033[92m"))
    else:
        click.echo(formatter._colorize(f"⚠️  目标 '{target_name}' 没有未处理告警", "\033[93m"))


@alert.command("mute")
@click.argument("target_name")
@click.option("--minutes", type=int, default=60, help="静音时长（分钟）")
@click.option("--config-dir", type=click.Path(), help="配置目录路径")
@click.pass_context
def mute_target(ctx, target_name, minutes, config_dir):
    """静音某个目标"""
    from pathlib import Path
    cm = ConfigManager(Path(config_dir)) if config_dir else ConfigManager()

    verbose = ctx.obj.get("verbose", False)
    quiet = ctx.obj.get("quiet", False)
    formatter = OutputFormatter(verbose=verbose, quiet=quiet)

    if minutes <= 0:
        raise ValidationError("静音时长必须大于 0 分钟")

    config = cm.load_config()
    if target_name not in config["targets"]:
        raise ValidationError(f"目标 '{target_name}' 不存在")

    cm.mute_target(target_name, minutes)
    click.echo(formatter._colorize(f"✅ 目标 '{target_name}' 已静音 {minutes} 分钟", "\033[92m"))


@alert.command("unmute")
@click.argument("target_name")
@click.option("--config-dir", type=click.Path(), help="配置目录路径")
@click.pass_context
def unmute_target(ctx, target_name, config_dir):
    """取消静音"""
    from pathlib import Path
    cm = ConfigManager(Path(config_dir)) if config_dir else ConfigManager()

    verbose = ctx.obj.get("verbose", False)
    quiet = ctx.obj.get("quiet", False)
    formatter = OutputFormatter(verbose=verbose, quiet=quiet)

    config = cm.load_config()
    if target_name not in config["targets"]:
        raise ValidationError(f"目标 '{target_name}' 不存在")

    cm.unmute_target(target_name)
    click.echo(formatter._colorize(f"✅ 目标 '{target_name}' 已取消静音", "\033[92m"))


@alert.command("anomalies")
@click.option("--limit", type=int, default=20, help="显示最近N条异常")
@click.option("--config-dir", type=click.Path(), help="配置目录路径")
@click.pass_context
def recent_anomalies(ctx, limit, config_dir):
    """查看最近异常"""
    from pathlib import Path
    cm = ConfigManager(Path(config_dir)) if config_dir else ConfigManager()

    verbose = ctx.obj.get("verbose", False)
    quiet = ctx.obj.get("quiet", False)
    formatter = OutputFormatter(verbose=verbose, quiet=quiet)

    alerts = cm.get_alerts()
    if not alerts:
        click.echo(formatter._colorize("✅ 最近无异常", "\033[92m"))
        return

    anomalies = alerts[:limit]

    if not quiet:
        click.echo(formatter._colorize(f"⚠️  最近 {len(anomalies)} 条异常:", "\033[93m"))

    for alert in anomalies:
        level = alert.get("level", "warning")
        status_color = "\033[91m" if level == "critical" else "\033[93m"
        icon = formatter._colorize("✗", "\033[91m")
        time_str = alert.get("timestamp", "")
        target = alert.get("target", "")
        msg = alert.get("message", "")
        click.echo(f"{icon} [{time_str}] {formatter._colorize(target, status_color)}: {msg}")


@alert.command("events")
@click.option("--target", help="按目标筛选")
@click.option("--only-active", is_flag=True, help="仅显示进行中的事件")
@click.option("--limit", type=int, default=20, help="显示条数限制")
@click.option("--config-dir", type=click.Path(), help="配置目录路径")
@click.pass_context
def list_events(ctx, target, only_active, limit, config_dir):
    """列出持续事件（合并的告警）"""
    from pathlib import Path
    cm = ConfigManager(Path(config_dir)) if config_dir else ConfigManager()

    verbose = ctx.obj.get("verbose", False)
    quiet = ctx.obj.get("quiet", False)
    formatter = OutputFormatter(verbose=verbose, quiet=quiet)

    events = cm.get_events(
        target=target,
        only_active=only_active,
        limit=limit
    )

    if not events:
        click.echo(formatter._colorize("✅ 当前无持续事件", "\033[92m"))
        return

    if not quiet:
        filters = []
        if target:
            filters.append(f"目标={target}")
        if only_active:
            filters.append("仅进行中")
        filter_str = f" (筛选: {', '.join(filters)})" if filters else ""
        click.echo(formatter._colorize(f"📋 持续事件列表 (共 {len(events)} 条{filter_str}):", "\033[94m"))
        if verbose:
            click.echo("-" * 100)

    for event in events:
        click.echo(formatter.format_event(event))

    if quiet:
        active = sum(1 for e in events if e.get("active"))
        closed = sum(1 for e in events if not e.get("active"))
        click.echo(formatter._colorize(
            f"共 {len(events)} 条事件, 进行中 {active}, 已结束 {closed}",
            "\033[93m"
        ))


@alert.command("set-threshold")
@click.option("--warning-ms", type=int, help="警告响应时间阈值(毫秒)")
@click.option("--critical-ms", type=int, help="严重响应时间阈值(毫秒)")
@click.option("--fail-count", type=int, help="连续失败次数阈值")
@click.option("--config-dir", type=click.Path(), help="配置目录路径")
@click.pass_context
def set_threshold(ctx, warning_ms, critical_ms, fail_count, config_dir):
    """设置告警阈值"""
    from pathlib import Path
    cm = ConfigManager(Path(config_dir)) if config_dir else ConfigManager()

    verbose = ctx.obj.get("verbose", False)
    quiet = ctx.obj.get("quiet", False)
    formatter = OutputFormatter(verbose=verbose, quiet=quiet)

    params = {}
    if warning_ms is not None:
        if warning_ms <= 0:
            raise ValidationError("警告响应时间阈值必须大于 0")
        params["response_time_warning"] = warning_ms
    if critical_ms is not None:
        if critical_ms <= 0:
            raise ValidationError("严重响应时间阈值必须大于 0")
        params["response_time_critical"] = critical_ms
    if fail_count is not None:
        if fail_count <= 0:
            raise ValidationError("连续失败次数阈值必须大于 0")
        params["consecutive_failures"] = fail_count

    if not params:
        raise ValidationError("请至少指定一个阈值参数")

    cm.update_thresholds(**params)

    msg_parts = []
    for k, v in params.items():
        if k == "response_time_warning":
            msg_parts.append(f"警告={v}ms")
        elif k == "response_time_critical":
            msg_parts.append(f"严重={v}ms")
        elif k == "consecutive_failures":
            msg_parts.append(f"连续失败={v}次")

    click.echo(formatter._colorize(f"✅ 阈值已更新: {', '.join(msg_parts)}", "\033[92m"))
