import click
from opsmonitor.config import ConfigManager, ValidationError
from opsmonitor.checker import HealthChecker, AlertManager
from opsmonitor.formatter import OutputFormatter


@click.command()
@click.argument("targets", nargs=-1)
@click.option("--group", help="按服务组检查")
@click.option("--all", "check_all", is_flag=True, help="检查所有目标")
@click.option("--group-by", "group_by", is_flag=True, default=True, help="按服务分组展示")
@click.option("--no-group", is_flag=True, help="不按服务分组展示")
@click.option("--save-history/--no-save-history", default=True, help="保存检查历史")
@click.option("--show-recovery/--no-show-recovery", default=True, help="显示恢复通知")
@click.option("--show-alerts/--no-show-alerts", default=True, help="显示新告警")
@click.option("--config-dir", type=click.Path(), help="配置目录路径")
@click.pass_context
def check(ctx, targets, group, check_all, group_by, no_group, save_history, show_recovery, show_alerts, config_dir):
    """手动执行连通性检查"""
    from pathlib import Path
    cm = ConfigManager(Path(config_dir)) if config_dir else ConfigManager()
    config = cm.load_config()

    verbose = ctx.obj.get("verbose", False)
    quiet = ctx.obj.get("quiet", False)
    formatter = OutputFormatter(verbose=verbose, quiet=quiet)

    settings = config["settings"]
    thresholds = config["thresholds"]
    all_targets = config["targets"]
    groups = config["groups"]

    if not all_targets:
        click.echo(formatter._colorize("⚠️  暂无监控目标，请先使用 'init add-target' 添加", "\033[93m"))
        return

    checker = HealthChecker(timeout=settings["timeout"], retries=settings["retries"])
    alert_manager = AlertManager(cm)

    targets_to_check = []

    if targets:
        for t in targets:
            if t in all_targets:
                targets_to_check.append(t)
            else:
                click.echo(formatter._colorize(f"⚠️  目标 '{t}' 不存在，已跳过", "\033[93m"))
    elif group:
        if group in groups:
            targets_to_check = groups[group]
        else:
            raise ValidationError(f"服务组 '{group}' 不存在")
    elif check_all:
        for g in groups.values():
            targets_to_check.extend(g)
    else:
        raise ValidationError("请指定目标、服务组或使用 --all")

    if not targets_to_check:
        click.echo(formatter._colorize("⚠️  没有可检查的目标", "\033[93m"))
        return

    results = []
    new_alerts = []
    recoveries = []

    if no_group:
        for target_name in targets_to_check:
            if target_name not in all_targets:
                continue
            target_config = all_targets[target_name]
            result = checker.check(target_config, target_name)
            results.append(result)

            muted = cm.is_muted(target_name)
            if not quiet:
                click.echo(formatter.format_check_result(result, thresholds, muted))

            if save_history:
                cm.add_history_entry(result.to_dict())

            if not muted:
                alert, event = alert_manager.check_alert(result, thresholds)
                if alert and show_alerts:
                    new_alerts.append((alert, event))

                recovery = alert_manager.check_recovery(result, thresholds)
                if recovery and show_recovery:
                    recoveries.append(recovery)
                    cm.clear_active_event(target_name)
    else:
        group_targets = {}
        for target_name in targets_to_check:
            if target_name not in all_targets:
                continue
            target_config = all_targets[target_name]
            g = target_config.get("group", "default")
            if g not in group_targets:
                group_targets[g] = []
            group_targets[g].append((target_name, target_config))

        for g, items in group_targets.items():
            if not quiet:
                click.echo(formatter.format_group_header(g, len(items)))
            for target_name, target_config in items:
                result = checker.check(target_config, target_name)
                results.append(result)

                muted = cm.is_muted(target_name)
                if not quiet:
                    click.echo(formatter.format_check_result(result, thresholds, muted))

                if save_history:
                    cm.add_history_entry(result.to_dict())

                if not muted:
                    alert, event = alert_manager.check_alert(result, thresholds)
                    if alert and show_alerts:
                        new_alerts.append((alert, event))

                    recovery = alert_manager.check_recovery(result, thresholds)
                    if recovery and show_recovery:
                        recoveries.append(recovery)
                        cm.clear_active_event(target_name)

    if new_alerts:
        if not quiet:
            click.echo()
        for alert, event in new_alerts:
            if not quiet:
                click.echo(formatter._colorize("🚨 新告警:", "\033[91m"))
            click.echo(formatter.format_alert(alert))
            if event and verbose and not quiet:
                click.echo(formatter._colorize("  关联事件:", "\033[93m"))
                click.echo(f"  {formatter.format_event(event)}")

    if recoveries:
        if not quiet:
            click.echo()
        for recovery in recoveries:
            click.echo(formatter.format_recovery(recovery))

    click.echo(formatter.format_summary(results, thresholds))
