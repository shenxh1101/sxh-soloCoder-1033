import click
from opsmonitor.config import ConfigManager, ValidationError, validate_positive_int
from opsmonitor.formatter import OutputFormatter


@click.group()
def init():
    """初始化配置和管理监控目标"""
    pass


@init.command()
@click.option("--config-dir", type=click.Path(), help="配置目录路径")
@click.pass_context
def config(ctx, config_dir):
    """初始化配置文件"""
    if config_dir:
        from pathlib import Path
        cm = ConfigManager(Path(config_dir))
    else:
        cm = ConfigManager()

    verbose = ctx.obj.get("verbose", False)
    quiet = ctx.obj.get("quiet", False)
    formatter = OutputFormatter(verbose=verbose, quiet=quiet)

    click.echo(formatter._colorize("✅ 配置已初始化完成", "\033[92m"))
    if not quiet:
        click.echo(f"  配置文件: {cm.config_file}")
        click.echo(f"  历史记录: {cm.history_file}")
        click.echo(f"  告警记录: {cm.alerts_file}")
        click.echo(f"  状态文件: {cm.state_file}")
        click.echo(f"  事件文件: {cm.events_file}")

    if verbose:
        config = cm.load_config()
        click.echo("\n当前设置:")
        for k, v in config["settings"].items():
            click.echo(f"  {k}: {v}")
        click.echo("\n当前阈值:")
        for k, v in config["thresholds"].items():
            click.echo(f"  {k}: {v}")


@init.command("add-target")
@click.argument("name")
@click.option("--type", "target_type", type=click.Choice(["http", "https", "tcp", "ping", "icmp"]), default="http", help="检查类型")
@click.option("--address", required=True, help="目标地址 (URL/IP/域名)")
@click.option("--port", type=int, help="端口号 (TCP必填, HTTP可选)")
@click.option("--group", default="default", help="服务组名称")
@click.option("--method", default="GET", help="HTTP请求方法")
@click.option("--expected-status", type=int, default=200, help="期望HTTP状态码")
@click.option("--config-dir", type=click.Path(), help="配置目录路径")
@click.pass_context
def add_target(ctx, name, target_type, address, port, group, method, expected_status, config_dir):
    """添加监控目标"""
    from pathlib import Path
    cm = ConfigManager(Path(config_dir)) if config_dir else ConfigManager()
    verbose = ctx.obj.get("verbose", False)
    quiet = ctx.obj.get("quiet", False)
    formatter = OutputFormatter(verbose=verbose, quiet=quiet)

    if target_type == "tcp" and port is None:
        raise ValidationError("TCP类型必须指定端口")

    if port is not None:
        validate_positive_int(port, "端口")

    success = cm.add_target(
        name=name,
        target_type=target_type,
        address=address,
        group=group,
        port=port,
        method=method,
        expected_status=expected_status
    )

    if success:
        click.echo(formatter._colorize(f"✅ 目标 '{name}' 已添加", "\033[92m"))
        if verbose and not quiet:
            config = cm.load_config()
            click.echo(formatter.format_target_config(name, config["targets"][name]))
    else:
        raise ValidationError(f"目标 '{name}' 已存在")


@init.command("remove-target")
@click.argument("name")
@click.option("--config-dir", type=click.Path(), help="配置目录路径")
@click.pass_context
def remove_target(ctx, name, config_dir):
    """移除监控目标"""
    from pathlib import Path
    cm = ConfigManager(Path(config_dir)) if config_dir else ConfigManager()
    verbose = ctx.obj.get("verbose", False)
    quiet = ctx.obj.get("quiet", False)
    formatter = OutputFormatter(verbose=verbose, quiet=quiet)

    success = cm.remove_target(name)
    if success:
        click.echo(formatter._colorize(f"✅ 目标 '{name}' 已移除", "\033[92m"))
    else:
        raise ValidationError(f"目标 '{name}' 不存在")


@init.command("list-targets")
@click.option("--group", help="按服务组筛选")
@click.option("--config-dir", type=click.Path(), help="配置目录路径")
@click.pass_context
def list_targets(ctx, group, config_dir):
    """列出所有监控目标"""
    from pathlib import Path
    cm = ConfigManager(Path(config_dir)) if config_dir else ConfigManager()
    verbose = ctx.obj.get("verbose", False)
    quiet = ctx.obj.get("quiet", False)
    formatter = OutputFormatter(verbose=verbose, quiet=quiet)

    config = cm.load_config()
    targets = config["targets"]
    groups = config["groups"]

    if not targets:
        click.echo(formatter._colorize("⚠️  暂无监控目标", "\033[93m"))
        return

    if group:
        if group not in groups:
            raise ValidationError(f"服务组 '{group}' 不存在")
        target_names = groups[group]
        if not quiet:
            click.echo(formatter.format_group_header(group, len(target_names)))
        for name in target_names:
            if name in targets:
                click.echo(formatter.format_target_config(name, targets[name]))
    else:
        for group_name, target_names in groups.items():
            if not quiet:
                click.echo(formatter.format_group_header(group_name, len(target_names)))
            for name in target_names:
                if name in targets:
                    click.echo(formatter.format_target_config(name, targets[name]))


@init.command("set-interval")
@click.argument("seconds", type=int)
@click.option("--config-dir", type=click.Path(), help="配置目录路径")
@click.pass_context
def set_interval(ctx, seconds, config_dir):
    """设置检查间隔（秒）"""
    from pathlib import Path
    cm = ConfigManager(Path(config_dir)) if config_dir else ConfigManager()
    verbose = ctx.obj.get("verbose", False)
    quiet = ctx.obj.get("quiet", False)
    formatter = OutputFormatter(verbose=verbose, quiet=quiet)

    validate_positive_int(seconds, "检查间隔")
    cm.update_settings(check_interval=seconds)
    click.echo(formatter._colorize(f"✅ 检查间隔已设置为 {seconds} 秒", "\033[92m"))


@init.command("set-timeout")
@click.argument("seconds", type=int)
@click.option("--config-dir", type=click.Path(), help="配置目录路径")
@click.pass_context
def set_timeout(ctx, seconds, config_dir):
    """设置超时时间（秒）"""
    from pathlib import Path
    cm = ConfigManager(Path(config_dir)) if config_dir else ConfigManager()
    verbose = ctx.obj.get("verbose", False)
    quiet = ctx.obj.get("quiet", False)
    formatter = OutputFormatter(verbose=verbose, quiet=quiet)

    validate_positive_int(seconds, "超时时间")
    cm.update_settings(timeout=seconds)
    click.echo(formatter._colorize(f"✅ 超时时间已设置为 {seconds} 秒", "\033[92m"))


@init.command("set-retries")
@click.argument("count", type=int)
@click.option("--config-dir", type=click.Path(), help="配置目录路径")
@click.pass_context
def set_retries(ctx, count, config_dir):
    """设置重试次数"""
    from pathlib import Path
    cm = ConfigManager(Path(config_dir)) if config_dir else ConfigManager()
    verbose = ctx.obj.get("verbose", False)
    quiet = ctx.obj.get("quiet", False)
    formatter = OutputFormatter(verbose=verbose, quiet=quiet)

    validate_positive_int(count, "重试次数")
    cm.update_settings(retries=count)
    click.echo(formatter._colorize(f"✅ 重试次数已设置为 {count} 次", "\033[92m"))
