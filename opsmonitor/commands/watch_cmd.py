import time
import click
import signal
from opsmonitor.config import ConfigManager
from opsmonitor.checker import HealthChecker, AlertManager
from opsmonitor.formatter import OutputFormatter


@click.command()
@click.argument("targets", nargs=-1)
@click.option("--group", help="按服务组观察")
@click.option("--all", "watch_all", is_flag=True, help="观察所有目标")
@click.option("--interval", type=int, help="检查间隔（秒），覆盖配置")
@click.option("--count", type=int, help="检查次数，达到后退出")
@click.option("--show-alerts/--no-show-alerts", default=True, help="实时显示告警")
@click.option("--config-dir", type=click.Path(), help="配置目录路径")
@click.pass_context
def watch(ctx, targets, group, watch_all, interval, count, show_alerts, config_dir):
    """持续观察响应时间"""
    from pathlib import Path
    cm = ConfigManager(Path(config_dir)) if config_dir else ConfigManager()
    config = cm.load_config()

    verbose = ctx.obj.get("verbose", False)
    formatter = OutputFormatter(verbose=verbose)

    settings = config["settings"]
    thresholds = config["thresholds"]
    all_targets = config["targets"]
    groups = config["groups"]

    if not all_targets:
        click.echo(formatter._colorize("⚠️  暂无监控目标，请先使用 'init add-target' 添加", "\033[93m"))
        return

    check_interval = interval if interval else settings["check_interval"]
    checker = HealthChecker(timeout=settings["timeout"], retries=settings["retries"])
    alert_manager = AlertManager(cm)

    targets_to_watch = []

    if targets:
        for t in targets:
            if t in all_targets:
                targets_to_watch.append(t)
            else:
                click.echo(formatter._colorize(f"⚠️  目标 '{t}' 不存在，已跳过", "\033[93m"))
    elif group:
        if group in groups:
            targets_to_watch = groups[group]
        else:
            click.echo(formatter._colorize(f"❌ 服务组 '{group}' 不存在", "\033[91m"))
            return
    elif watch_all:
        for g in groups.values():
            targets_to_watch.extend(g)
    else:
        click.echo(formatter._colorize("⚠️  请指定目标、服务组或使用 --all", "\033[93m"))
        return

    if not targets_to_watch:
        click.echo(formatter._colorize("⚠️  没有可观察的目标", "\033[93m"))
        return

    click.echo(formatter._colorize(f"🔍 开始监控 {len(targets_to_watch)} 个目标，间隔 {check_interval} 秒", "\033[96m"))
    click.echo(formatter._colorize("按 Ctrl+C 停止监控\n", "\033[90m"))
    click.echo(formatter.format_watch_header())
    click.echo("-" * 80)

    running = True
    check_count = 0

    def signal_handler(signum, frame):
        nonlocal running
        running = False
        click.echo()
        click.echo(formatter._colorize("\n⏹️  正在停止监控...", "\033[93m"))

    signal.signal(signal.SIGINT, signal_handler)

    try:
        while running:
            all_results = []
            for target_name in targets_to_watch:
                if not running:
                    break
                if target_name not in all_targets:
                    continue

                target_config = all_targets[target_name]
                result = checker.check(target_config, target_name)
                all_results.append(result)

                muted = cm.is_muted(target_name)
                if not muted:
                    click.echo(formatter.format_watch_line(result, thresholds))
                else:
                    muted_line = formatter.format_watch_line(result, thresholds)
                    click.echo(formatter._colorize(muted_line, "\033[90m"))

                cm.add_history_entry(result.to_dict())

                if not muted:
                    alert = alert_manager.check_alert(result, thresholds)
                    if alert and show_alerts:
                        click.echo()
                        click.echo(formatter._colorize("🚨 新告警:", "\033[91m"))
                        click.echo(formatter.format_alert(alert))
                        click.echo()

            check_count += 1
            if count and check_count >= count:
                running = False
                break

            if running:
                for _ in range(check_interval):
                    if not running:
                        break
                    time.sleep(1)

    except Exception as e:
        click.echo(formatter._colorize(f"\n❌ 监控出错: {str(e)}", "\033[91m"))
    finally:
        click.echo()
        click.echo(formatter.format_summary(all_results, thresholds))
        click.echo(formatter._colorize(f"\n✅ 监控已结束，共执行 {check_count} 轮检查", "\033[92m"))
