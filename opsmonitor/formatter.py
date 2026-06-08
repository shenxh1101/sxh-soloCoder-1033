from datetime import datetime
from typing import Dict, List, Optional


class Colors:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RED = "\033[91m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    MAGENTA = "\033[95m"
    CYAN = "\033[96m"
    GRAY = "\033[90m"

    @staticmethod
    def level_color(level: str) -> str:
        return {
            "ok": Colors.GREEN,
            "warning": Colors.YELLOW,
            "critical": Colors.RED,
            "muted": Colors.GRAY
        }.get(level, Colors.RESET)

    @staticmethod
    def status_icon(level: str) -> str:
        return {
            "ok": "✓",
            "warning": "⚠",
            "critical": "✗",
            "muted": "🔇"
        }.get(level, "•")


class OutputFormatter:
    def __init__(self, verbose: bool = False, use_color: bool = True):
        self.verbose = verbose
        self.use_color = use_color

    def _colorize(self, text: str, color: str) -> str:
        if self.use_color:
            return f"{color}{text}{Colors.RESET}"
        return text

    def _format_time(self, timestamp: int) -> str:
        return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")

    def format_check_result(self, result, thresholds: Dict, muted: bool = False) -> str:
        if muted:
            level = "muted"
        else:
            level = result.get_level(thresholds)

        icon = self._colorize(Colors.status_icon(level), Colors.level_color(level))
        level_text = self._colorize(level.upper().ljust(8), Colors.level_color(level))
        target = self._colorize(result.target.ljust(20), Colors.BOLD)

        if result.success:
            time_str = f"{result.response_time:7.1f}ms"
            if level == "critical":
                time_str = self._colorize(time_str, Colors.RED)
            elif level == "warning":
                time_str = self._colorize(time_str, Colors.YELLOW)
            else:
                time_str = self._colorize(time_str, Colors.GREEN)
        else:
            time_str = self._colorize(f"{'FAILED':>7}", Colors.RED)

        status_code = f" [HTTP {result.status_code}]" if result.status_code else ""
        error = f" - {result.error}" if result.error and not result.success else ""

        line = f"{icon} {level_text} {target} {time_str}{status_code}{error}"

        if self.verbose and result.details:
            details = []
            for k, v in result.details.items():
                if isinstance(v, dict):
                    continue
                details.append(f"    {k}: {v}")
            if details:
                line += "\n" + "\n".join(details)

        return line

    def format_group_header(self, group_name: str, count: int) -> str:
        header = f"\n{'=' * 60}"
        header += f"\n{self._colorize(f'📦 服务组: {group_name} ({count} 个目标)', Colors.BOLD + Colors.BLUE)}"
        header += f"\n{'=' * 60}"
        return header

    def format_summary(self, results: List, thresholds: Dict) -> str:
        total = len(results)
        ok_count = sum(1 for r in results if r.success and r.get_level(thresholds) == "ok")
        warning_count = sum(1 for r in results if r.get_level(thresholds) == "warning")
        critical_count = sum(1 for r in results if r.get_level(thresholds) == "critical" or not r.success)

        lines = [
            "",
            "=" * 60,
            self._colorize("📊 检查汇总", Colors.BOLD + Colors.CYAN),
            "=" * 60,
            f"  总计: {total} 个目标",
            f"  {self._colorize('✓ 正常', Colors.GREEN)}: {ok_count}",
            f"  {self._colorize('⚠ 警告', Colors.YELLOW)}: {warning_count}",
            f"  {self._colorize('✗ 严重', Colors.RED)}: {critical_count}"
        ]

        if total > 0:
            success_rate = (ok_count + warning_count) / total * 100
            bar_length = 30
            filled = int(success_rate / 100 * bar_length)
            bar = "█" * filled + "░" * (bar_length - filled)
            color = Colors.GREEN if success_rate >= 90 else (Colors.YELLOW if success_rate >= 70 else Colors.RED)
            lines.append(f"  成功率: {self._colorize(bar, color)} {success_rate:.1f}%")

        return "\n".join(lines)

    def format_alert(self, alert: Dict) -> str:
        level = alert.get("level", "warning")
        icon = self._colorize(Colors.status_icon(level), Colors.level_color(level))
        level_text = self._colorize(level.upper().ljust(8), Colors.level_color(level))
        time_str = self._format_time(alert.get("timestamp", 0))
        target = self._colorize(alert.get("target", "").ljust(15), Colors.BOLD)
        message = alert.get("message", "")
        alert_id = alert.get("id", "")[:8]
        handled = "✓" if alert.get("handled", False) else " "

        line = f"{handled} {icon} {level_text} [{time_str}] {target} {message}"

        if self.verbose:
            lines = [line]
            lines.append(f"    ID: {alert_id}...  类型: {alert.get('type', '')}")
            if alert.get("response_time"):
                lines.append(f"    响应时间: {alert['response_time']:.1f}ms")
            if alert.get("consecutive_failures"):
                lines.append(f"    连续失败: {alert['consecutive_failures']} 次")
            if alert.get("handled"):
                lines.append(f"    处理备注: {alert.get('handled_note', '')}")
                lines.append(f"    处理时间: {self._format_time(alert.get('handled_at', 0))}")
            return "\n".join(lines)

        return line

    def format_watch_header(self) -> str:
        return self._colorize(
            f"{'时间':^19} | {'目标':^15} | {'状态':^8} | {'响应时间':^10} | 消息",
            Colors.BOLD + Colors.CYAN
        )

    def format_watch_line(self, result, thresholds: Dict) -> str:
        level = result.get_level(thresholds)
        time_str = self._format_time(result.timestamp)
        target = result.target[:15].ljust(15)
        level_text = self._colorize(level.upper().ljust(8), Colors.level_color(level))

        if result.success:
            time_val = f"{result.response_time:>8.1f}ms"
            if level == "critical":
                time_val = self._colorize(time_val, Colors.RED)
            elif level == "warning":
                time_val = self._colorize(time_val, Colors.YELLOW)
            else:
                time_val = self._colorize(time_val, Colors.GREEN)
            msg = ""
        else:
            time_val = self._colorize(f"{'FAILED':>8}", Colors.RED)
            msg = result.error or ""

        return f"{time_str} | {target} | {level_text} | {time_val} | {msg}"

    def format_muted_target(self, name: str, mute_info: Dict) -> str:
        until = self._format_time(mute_info.get("until", 0))
        reason = mute_info.get("reason", "")
        icon = self._colorize("🔇", Colors.GRAY)
        return f"{icon} {self._colorize(name.ljust(20), Colors.BOLD)} 静音至: {until} 原因: {reason}"

    def format_target_config(self, name: str, config: Dict) -> str:
        target_type = config.get("type", "http")
        address = config.get("address", "")
        port = f":{config.get('port')}" if config.get("port") else ""
        group = config.get("group", "default")
        enabled = "✓" if config.get("enabled", True) else "✗"

        line = f"  {self._colorize(name.ljust(18), Colors.BOLD)} {target_type.ljust(6)} {address}{port}"

        if self.verbose:
            line += f"\n    组: {group} | 方法: {config.get('method', 'GET')} | 期望状态: {config.get('expected_status', 200)} | 启用: {enabled}"

        return line

    def format_timeline_event(self, event: Dict) -> str:
        time_str = self._format_time(event.get("timestamp", 0))
        level = event.get("level", "info")
        target = event.get("target", "")
        message = event.get("message", "")

        icon = self._colorize(Colors.status_icon(level), Colors.level_color(level))
        level_text = self._colorize(level.upper().ljust(8), Colors.level_color(level))

        return f"[{time_str}] {icon} {level_text} {target}: {message}"

    def format_report_section(self, title: str, content: str) -> str:
        lines = [
            "",
            "=" * 60,
            self._colorize(f"📋 {title}", Colors.BOLD + Colors.MAGENTA),
            "=" * 60,
            content
        ]
        return "\n".join(lines)
