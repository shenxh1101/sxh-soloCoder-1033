from datetime import datetime
from typing import Dict, List, Optional
import statistics


def percentile(data: List[float], p: float) -> float:
    if not data:
        return 0.0
    if len(data) == 1:
        return data[0]
    sorted_data = sorted(data)
    k = (len(sorted_data) - 1) * (p / 100)
    f = int(k)
    c = f + 1
    if c >= len(sorted_data):
        return sorted_data[-1]
    return sorted_data[f] + (sorted_data[c] - sorted_data[f]) * (k - f)


def p95(data: List[float]) -> float:
    return percentile(data, 95)


def p99(data: List[float]) -> float:
    return percentile(data, 99)


def format_duration(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds}秒"
    elif seconds < 3600:
        return f"{seconds // 60}分{seconds % 60}秒"
    else:
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        secs = seconds % 60
        if secs == 0:
            return f"{hours}小时{minutes}分"
        return f"{hours}小时{minutes}分{secs}秒"


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
            "muted": Colors.GRAY,
            "recovery": Colors.GREEN
        }.get(level, Colors.RESET)

    @staticmethod
    def status_icon(level: str) -> str:
        return {
            "ok": "✓",
            "warning": "⚠",
            "critical": "✗",
            "muted": "🔇",
            "recovery": "↻"
        }.get(level, "•")


class OutputFormatter:
    def __init__(self, verbose: bool = False, quiet: bool = False, use_color: bool = True):
        self.verbose = verbose and not quiet
        self.quiet = quiet
        self.use_color = use_color

    def _colorize(self, text: str, color: str) -> str:
        if self.use_color:
            return f"{color}{text}{Colors.RESET}"
        return text

    def _format_time(self, timestamp) -> str:
        if isinstance(timestamp, str):
            try:
                dt = datetime.strptime(timestamp, "%Y-%m-%d %H:%M:%S")
                return dt.strftime("%Y-%m-%d %H:%M:%S")
            except ValueError:
                return timestamp
        return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")

    def _timestamp_to_int(self, timestamp) -> int:
        if isinstance(timestamp, str):
            try:
                dt = datetime.strptime(timestamp, "%Y-%m-%d %H:%M:%S")
                return int(dt.timestamp())
            except ValueError:
                return 0
        return timestamp

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
        if self.quiet:
            return ""
        header = f"\n{'=' * 60}"
        header += f"\n{self._colorize(f'📦 服务组: {group_name} ({count} 个目标)', Colors.BOLD + Colors.BLUE)}"
        header += f"\n{'=' * 60}"
        return header

    def format_summary(self, results: List, thresholds: Dict) -> str:
        total = len(results)
        ok_count = sum(1 for r in results if r.success and r.get_level(thresholds) == "ok")
        warning_count = sum(1 for r in results if r.get_level(thresholds) == "warning")
        critical_count = sum(1 for r in results if r.get_level(thresholds) == "critical" or not r.success)

        if self.quiet:
            lines = [
                f"检查完成: {total}个目标 | "
                f"{self._colorize(f'正常{ok_count}', Colors.GREEN)} | "
                f"{self._colorize(f'警告{warning_count}', Colors.YELLOW)} | "
                f"{self._colorize(f'严重{critical_count}', Colors.RED)}"
            ]
            if total > 0:
                success_rate = (ok_count + warning_count) / total * 100
                if success_rate >= 90:
                    rate_color = Colors.GREEN
                elif success_rate >= 70:
                    rate_color = Colors.YELLOW
                else:
                    rate_color = Colors.RED
                lines[0] += f" | 成功率: {self._colorize(f'{success_rate:.1f}%', rate_color)}"
            return "\n".join(lines)

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
        level = alert.get("last_level", alert.get("level", "warning"))
        icon = self._colorize(Colors.status_icon(level), Colors.level_color(level))
        level_text = self._colorize(level.upper().ljust(8), Colors.level_color(level))

        count = alert.get("count", 1)
        if count > 1:
            count_str = self._colorize(f" x{count}", Colors.YELLOW)
        else:
            count_str = ""

        first_ts = alert.get("first_timestamp", alert.get("timestamp", 0))
        last_ts = alert.get("last_timestamp", alert.get("timestamp", 0))

        if count > 1:
            first_str = self._format_time(first_ts)
            last_str = self._format_time(last_ts)
            time_str = f"[{first_str} → {last_str}]"
        else:
            time_str = f"[{self._format_time(last_ts)}]"

        target = self._colorize(alert.get("target", "").ljust(15), Colors.BOLD)
        message = alert.get("last_message", alert.get("message", ""))
        alert_id = alert.get("id", "")[:8]
        handled = "✓" if alert.get("handled", False) else " "
        event_id = alert.get("event_id", "")[:8] if alert.get("event_id") else ""

        line = f"{handled} {icon} {level_text} {time_str} {target}{count_str} {message}"

        if self.quiet:
            return line

        if self.verbose:
            lines = [line]
            lines.append(f"    ID: {alert_id}...  事件ID: {event_id}...  类型: {alert.get('type', '')}")
            if count > 1:
                lines.append(f"    累计次数: {count}")
                lines.append(f"    首次发生: {self._format_time(first_ts)}")
                lines.append(f"    最后更新: {self._format_time(last_ts)}")
                if alert.get("first_level") != alert.get("last_level"):
                    lines.append(f"    级别变化: {alert.get('first_level')} → {alert.get('last_level')}")
            rt = alert.get("last_response_time", alert.get("response_time"))
            if rt:
                lines.append(f"    响应时间: {rt:.1f}ms")
            if alert.get("consecutive_failures"):
                lines.append(f"    连续失败: {alert['consecutive_failures']} 次")
            if alert.get("handled"):
                handler = alert.get("handler", "")
                conclusion = alert.get("conclusion", "")
                recovery_time = alert.get("recovery_time")
                lines.append(f"    处理人: {handler}")
                lines.append(f"    处理结论: {conclusion}")
                lines.append(f"    处理备注: {alert.get('handled_note', '')}")
                if recovery_time:
                    lines.append(f"    恢复时间: {self._format_time(recovery_time)}")
                lines.append(f"    处理时间: {self._format_time(alert.get('handled_at', 0))}")
            return "\n".join(lines)

        if event_id and count == 1:
            line += f" [{event_id}...]"
        return line

    def format_event(self, event: Dict) -> str:
        target = event.get("target", "")
        start_time = self._format_time(event.get("start_time", 0))
        last_update = self._format_time(event.get("last_update", 0))
        alert_count = event.get("alert_count", 1)
        first_level = event.get("first_level", "warning")
        last_level = event.get("last_level", "warning")
        has_critical = event.get("has_critical", False)
        closed = event.get("closed", False)
        duration = event.get("duration_seconds")
        recovery_method = event.get("recovery_method", "")

        if recovery_method == "auto":
            recovery_method_str = "自动恢复"
        elif recovery_method == "manual":
            recovery_method_str = "手动处理"
        else:
            recovery_method_str = ""

        status_icon = "🔒" if closed else "🔥"
        status_text = self._colorize("已关闭", Colors.GRAY) if closed else self._colorize("进行中", Colors.RED)
        level_icon = self._colorize(
            Colors.status_icon("critical" if has_critical else last_level),
            Colors.level_color("critical" if has_critical else last_level)
        )

        duration_str = ""
        if duration:
            duration_str = f" | 持续: {format_duration(duration)}"
        elif not closed:
            start_ts = self._timestamp_to_int(event.get("start_time", 0))
            current_duration = int(datetime.now().timestamp()) - start_ts
            duration_str = f" | 已持续: {format_duration(current_duration)}"

        recovery_method_display = f" | 恢复方式: {recovery_method_str}" if closed and recovery_method_str else ""

        event_id_short = event.get("id", "")[:8]

        line = (f"{status_icon} [{event_id_short}...] {self._colorize(target.ljust(18), Colors.BOLD)} "
                f"{level_icon} {alert_count} 条告警 | 开始: {start_time} | 最后更新: {last_update}"
                f"{duration_str}{recovery_method_display} | {status_text}")

        if self.verbose:
            lines = [line]
            lines.append(f"    首次告警: {event.get('first_message', '')}")
            lines.append(f"    最后告警: {event.get('last_message', '')}")
            lines.append(f"    级别变化: {first_level.upper()} -> {last_level.upper()}")
            if closed:
                recovery_time = event.get("recovery_time")
                close_time = event.get("close_time")
                if recovery_time:
                    lines.append(f"    恢复时间: {self._format_time(recovery_time)}")
                if close_time:
                    lines.append(f"    处理时间: {self._format_time(close_time)}")
                lines.append(f"    处理人: {event.get('close_handler', '')}")
                lines.append(f"    处理结论: {event.get('close_conclusion', '')}")
                lines.append(f"    处理备注: {event.get('close_note', '')}")
                if recovery_method_str:
                    lines.append(f"    恢复方式: {recovery_method_str}")
            timeline = event.get("timeline", [])
            if timeline and len(timeline) > 0:
                lines.append("")
                lines.append(self._colorize("    📅 事件时间线:", Colors.BOLD + Colors.CYAN))
                for entry in timeline:
                    entry_type = entry.get("type", "")
                    entry_ts = self._format_time(entry.get("timestamp", 0))
                    if entry_type == "start":
                        level = entry.get("level", "")
                        msg = entry.get("message", "")
                        lines.append(f"      ➜ {entry_ts} 开始: {level.upper()} - {msg}")
                    elif entry_type == "level_change":
                        level = entry.get("level", "")
                        msg = entry.get("message", "")
                        rt = entry.get("response_time", 0)
                        lines.append(f"      ➜ {entry_ts} 级别变化: {level.upper()} - {msg} ({rt:.0f}ms)")
                    elif entry_type == "update":
                        level = entry.get("level", "")
                        msg = entry.get("message", "")
                        rt = entry.get("response_time", 0)
                        lines.append(f"      ➜ {entry_ts} 更新: {level.upper()} - {msg} ({rt:.0f}ms)")
                    elif entry_type == "recovery":
                        method = entry.get("method", "")
                        method_display = "自动恢复" if method == "auto" else "手动处理"
                        handler = entry.get("handler", "")
                        conclusion = entry.get("conclusion", "")
                        lines.append(f"      ➜ {entry_ts} 结束: {method_display} - {handler} - {conclusion}")
            return "\n".join(lines)

        return line

    def format_watch_header(self) -> str:
        if self.quiet:
            return ""
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

    def format_recovery(self, recovery_info: Dict) -> str:
        target = recovery_info.get("target", "")
        duration = recovery_info.get("duration_seconds", 0)
        alert_count = recovery_info.get("alert_count", 1)
        response_time = recovery_info.get("response_time", 0)
        time_str = self._format_time(recovery_info.get("timestamp", 0))

        icon = self._colorize("↻", Colors.GREEN)
        return (f"{self._colorize('✅ 服务恢复', Colors.BOLD + Colors.GREEN)} "
                f"[{time_str}] {self._colorize(target, Colors.BOLD)} "
                f"已恢复正常 | 响应时间: {response_time:.1f}ms | "
                f"故障持续: {format_duration(duration)} | 共 {alert_count} 条告警")

    def format_muted_target(self, name: str, mute_info: Dict) -> str:
        until = self._format_time(mute_info.get("until", 0))
        reason = mute_info.get("reason", "")
        remaining = mute_info.get("until", 0) - int(datetime.now().timestamp())
        remaining_str = format_duration(max(0, remaining)) if remaining > 0 else "已过期"
        icon = self._colorize("🔇", Colors.GRAY)
        return f"{icon} {self._colorize(name.ljust(20), Colors.BOLD)} 剩余: {remaining_str} | 静音至: {until} | 原因: {reason}"

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
        if self.quiet and content.strip():
            return content
        lines = [
            "",
            "=" * 60,
            self._colorize(f"📋 {title}", Colors.BOLD + Colors.MAGENTA),
            "=" * 60,
            content
        ]
        return "\n".join(lines)

    def format_stats(self, label: str, values: List[float], unit: str = "ms") -> str:
        if not values:
            return f"  {label.ljust(20)}: 无数据"

        avg = statistics.mean(values)
        med = statistics.median(values)
        p95_val = p95(values)
        p99_val = p99(values)
        max_val = max(values)
        min_val = min(values)

        if self.quiet:
            return (f"  {label}: 平均{avg:.0f}{unit} | P95{p95_val:.0f}{unit} | "
                    f"P99{p99_val:.0f}{unit} | 最大{max_val:.0f}{unit}")

        return (f"  {label.ljust(20)}: 平均{avg:.0f}{unit} | 中位{med:.0f}{unit} | "
                f"P95{p95_val:.0f}{unit} | P99{p99_val:.0f}{unit} | "
                f"最大{max_val:.0f}{unit} | 最小{min_val:.0f}{unit}")

    def format_availability(self, label: str, success_count: int, total_count: int) -> str:
        if total_count == 0:
            rate = 0.0
        else:
            rate = success_count / total_count * 100

        color = Colors.GREEN if rate >= 99.9 else (Colors.YELLOW if rate >= 99 else Colors.RED)
        rate_str = self._colorize(f"{rate:.3f}%", color)

        if self.quiet:
            return f"  {label}: {rate_str}"

        bar_length = 30
        filled = int(rate / 100 * bar_length)
        bar = "█" * filled + "░" * (bar_length - filled)
        return f"  {label.ljust(20)}: {self._colorize(bar, color)} {rate_str} ({success_count}/{total_count})"

    def format_failure_ranking(self, rankings: List[Dict]) -> str:
        if not rankings:
            return "  无故障记录"

        lines = []
        for i, item in enumerate(rankings, 1):
            target = item.get("target", "")
            failures = item.get("failures", 0)
            longest_outage = item.get("longest_outage", 0)
            total_downtime = item.get("total_downtime", 0)

            rank_color = Colors.RED if i <= 3 else (Colors.YELLOW if i <= 5 else Colors.RESET)
            rank_str = self._colorize(f"#{i}".ljust(3), rank_color)

            if self.quiet:
                lines.append(f"  {rank_str} {target.ljust(18)} {failures}次故障 | "
                            f"累计停机{format_duration(total_downtime)} | "
                            f"最长{format_duration(longest_outage)}")
            else:
                lines.append(f"  {rank_str} {self._colorize(target.ljust(18), Colors.BOLD)} "
                            f"故障{failures}次 | 累计停机{format_duration(total_downtime)} | "
                            f"最长单次{format_duration(longest_outage)}")

        return "\n".join(lines)
