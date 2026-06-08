#!/usr/bin/env python3
import click
import sys
from opsmonitor.commands.init_cmd import init
from opsmonitor.commands.check_cmd import check
from opsmonitor.commands.watch_cmd import watch
from opsmonitor.commands.alert_cmd import alert
from opsmonitor.commands.report_cmd import report
from opsmonitor.formatter import Colors
from opsmonitor.config import ValidationError


@click.group()
@click.option("--verbose", "-v", is_flag=True, help="详细输出模式")
@click.option("--quiet", "-q", is_flag=True, help="简洁输出模式")
@click.option("--no-color", is_flag=True, help="禁用彩色输出")
@click.pass_context
def cli(ctx, verbose, quiet, no_color):
    """运维监控命令行工具 - 快速查看服务健康情况和处理告警"""
    ctx.ensure_object(dict)
    ctx.obj["verbose"] = verbose and not quiet
    ctx.obj["quiet"] = quiet
    ctx.obj["no_color"] = no_color

    if no_color:
        import os
        os.environ["NO_COLOR"] = "1"


cli.add_command(init)
cli.add_command(check)
cli.add_command(watch)
cli.add_command(alert)
cli.add_command(report)


@cli.command("help")
@click.argument("command", required=False)
@click.pass_context
def help_cmd(ctx, command):
    """显示帮助信息"""
    if command:
        cmd_obj = cli.commands.get(command)
        if cmd_obj:
            click.echo(cmd_obj.get_help(ctx))
        else:
            click.echo(f"{Colors.RED}未知命令: {command}{Colors.RESET}")
            click.echo(f"可用命令: {', '.join(cli.commands.keys())}")
    else:
        click.echo(cli.get_help(ctx))


def main():
    try:
        cli(obj={})
    except KeyboardInterrupt:
        click.echo()
        click.echo(f"{Colors.YELLOW}⏹️  操作已取消{Colors.RESET}")
        sys.exit(130)
    except ValidationError as e:
        click.echo(f"{Colors.RED}❌ 参数错误: {str(e)}{Colors.RESET}")
        sys.exit(2)
    except Exception as e:
        click.echo(f"{Colors.RED}❌ 错误: {str(e)}{Colors.RESET}")
        sys.exit(1)


if __name__ == "__main__":
    main()
