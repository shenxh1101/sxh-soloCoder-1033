import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple

DEFAULT_CONFIG_DIR = Path.home() / ".opsmonitor"
DEFAULT_CONFIG_FILE = DEFAULT_CONFIG_DIR / "config.json"
DEFAULT_HISTORY_FILE = DEFAULT_CONFIG_DIR / "history.json"
DEFAULT_ALERTS_FILE = DEFAULT_CONFIG_DIR / "alerts.json"
DEFAULT_STATE_FILE = DEFAULT_CONFIG_DIR / "state.json"
DEFAULT_EVENTS_FILE = DEFAULT_CONFIG_DIR / "events.json"

DEFAULT_CONFIG = {
    "targets": {},
    "groups": {},
    "settings": {
        "check_interval": 60,
        "timeout": 10,
        "retries": 2,
        "verbose": False
    },
    "thresholds": {
        "response_time_warning": 500,
        "response_time_critical": 2000,
        "consecutive_failures": 3
    },
    "muted": {}
}

DEFAULT_STATE = {
    "consecutive_failures": {},
    "last_status": {},
    "active_events": {}
}


class ValidationError(Exception):
    pass


def validate_positive_int(value: int, field_name: str) -> None:
    if not isinstance(value, int) or value <= 0:
        raise ValidationError(f"{field_name} 必须是大于 0 的整数，当前值: {value}")


class ConfigManager:
    def __init__(self, config_dir: Optional[Path] = None):
        self.config_dir = config_dir or DEFAULT_CONFIG_DIR
        self.config_file = self.config_dir / "config.json"
        self.history_file = self.config_dir / "history.json"
        self.alerts_file = self.config_dir / "alerts.json"
        self.state_file = self.config_dir / "state.json"
        self.events_file = self.config_dir / "events.json"
        self._ensure_dir()

    def _ensure_dir(self):
        self.config_dir.mkdir(parents=True, exist_ok=True)
        if not self.config_file.exists():
            self.save_config(DEFAULT_CONFIG)
        if not self.history_file.exists():
            self._save_json(self.history_file, [])
        if not self.alerts_file.exists():
            self._save_json(self.alerts_file, [])
        if not self.state_file.exists():
            self._save_json(self.state_file, DEFAULT_STATE)
        if not self.events_file.exists():
            self._save_json(self.events_file, [])

    def _load_json(self, path: Path) -> Any:
        if path.exists():
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        return None

    def _save_json(self, path: Path, data: Any):
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def load_config(self) -> Dict:
        config = self._load_json(self.config_file) or DEFAULT_CONFIG
        for key, value in DEFAULT_CONFIG.items():
            if key not in config:
                config[key] = value
            elif isinstance(value, dict):
                for k, v in value.items():
                    if k not in config[key]:
                        config[key][k] = v
        return config

    def save_config(self, config: Dict):
        self._save_json(self.config_file, config)

    def load_state(self) -> Dict:
        state = self._load_json(self.state_file) or DEFAULT_STATE
        for key, value in DEFAULT_STATE.items():
            if key not in state:
                state[key] = value
            elif isinstance(value, dict):
                for k, v in value.items():
                    if k not in state[key]:
                        state[key][k] = v
        return state

    def save_state(self, state: Dict):
        self._save_json(self.state_file, state)

    def get_consecutive_failures(self, target: str) -> int:
        state = self.load_state()
        return state["consecutive_failures"].get(target, 0)

    def set_consecutive_failures(self, target: str, count: int) -> None:
        state = self.load_state()
        if count <= 0:
            if target in state["consecutive_failures"]:
                del state["consecutive_failures"][target]
        else:
            state["consecutive_failures"][target] = count
        self.save_state(state)

    def increment_consecutive_failures(self, target: str) -> int:
        count = self.get_consecutive_failures(target) + 1
        self.set_consecutive_failures(target, count)
        return count

    def reset_consecutive_failures(self, target: str) -> None:
        self.set_consecutive_failures(target, 0)

    def get_last_status(self, target: str) -> Optional[str]:
        state = self.load_state()
        return state["last_status"].get(target)

    def set_last_status(self, target: str, status: str) -> None:
        state = self.load_state()
        state["last_status"][target] = status
        self.save_state(state)

    def add_target(self, name: str, target_type: str, address: str,
                   group: str = "default", port: Optional[int] = None,
                   method: str = "GET", expected_status: int = 200) -> bool:
        config = self.load_config()
        if name in config["targets"]:
            return False
        config["targets"][name] = {
            "type": target_type,
            "address": address,
            "port": port,
            "method": method,
            "expected_status": expected_status,
            "group": group,
            "enabled": True
        }
        if group not in config["groups"]:
            config["groups"][group] = []
        if name not in config["groups"][group]:
            config["groups"][group].append(name)
        self.save_config(config)
        return True

    def remove_target(self, name: str) -> bool:
        config = self.load_config()
        if name not in config["targets"]:
            return False
        target = config["targets"].pop(name)
        group = target.get("group", "default")
        if group in config["groups"] and name in config["groups"][group]:
            config["groups"][group].remove(name)
            if not config["groups"][group]:
                del config["groups"][group]
        if name in config["muted"]:
            del config["muted"][name]

        state = self.load_state()
        for key in ["consecutive_failures", "last_status", "active_events"]:
            if name in state[key]:
                del state[key][name]
        self.save_state(state)

        self.save_config(config)
        return True

    def update_thresholds(self, **kwargs) -> None:
        for key, value in kwargs.items():
            if key in ["response_time_warning", "response_time_critical", "consecutive_failures"]:
                validate_positive_int(value, key)
        config = self.load_config()
        for key, value in kwargs.items():
            if key in config["thresholds"]:
                config["thresholds"][key] = value
        self.save_config(config)

    def update_settings(self, **kwargs) -> None:
        for key, value in kwargs.items():
            if key in ["check_interval", "timeout", "retries"]:
                validate_positive_int(value, key)
        config = self.load_config()
        for key, value in kwargs.items():
            if key in config["settings"]:
                config["settings"][key] = value
        self.save_config(config)

    def mute_target(self, name: str, duration_minutes: int = 60, reason: str = "") -> bool:
        config = self.load_config()
        if name not in config["targets"]:
            return False
        validate_positive_int(duration_minutes, "静音时长")
        config["muted"][name] = {
            "until": int(time.time()) + duration_minutes * 60,
            "reason": reason
        }
        self.save_config(config)
        return True

    def unmute_target(self, name: str) -> bool:
        config = self.load_config()
        if name in config["muted"]:
            del config["muted"][name]
            self.save_config(config)
            return True
        return False

    def is_muted(self, name: str) -> bool:
        config = self.load_config()
        if name not in config["muted"]:
            return False
        if config["muted"][name]["until"] < int(time.time()):
            del config["muted"][name]
            self.save_config(config)
            return False
        return True

    def get_mute_info(self, name: str) -> Optional[Dict]:
        config = self.load_config()
        if name in config["muted"]:
            if config["muted"][name]["until"] >= int(time.time()):
                return config["muted"][name]
            else:
                del config["muted"][name]
                self.save_config(config)
        return None

    def get_targets_by_group(self) -> Dict[str, List[str]]:
        config = self.load_config()
        return config["groups"]

    def get_target_group(self, target_name: str) -> str:
        config = self.load_config()
        target = config["targets"].get(target_name, {})
        return target.get("group", "default")

    def add_history_entry(self, entry: Dict):
        history = self._load_json(self.history_file) or []
        history.append(entry)
        if len(history) > 10000:
            history = history[-10000:]
        self._save_json(self.history_file, history)

    def get_history(self, target_name: Optional[str] = None,
                    limit: int = 100, only_errors: bool = False,
                    start_time: Optional[datetime] = None,
                    end_time: Optional[datetime] = None) -> List[Dict]:
        history = self._load_json(self.history_file) or []
        if target_name:
            history = [h for h in history if h.get("target") == target_name]
        if only_errors:
            history = [h for h in history if not h.get("success", True)]
        if start_time:
            start_ts = int(start_time.timestamp())
            history = [h for h in history if self._timestamp_to_int(h.get("timestamp", 0)) >= start_ts]
        if end_time:
            end_ts = int(end_time.timestamp())
            history = [h for h in history if self._timestamp_to_int(h.get("timestamp", 0)) <= end_ts]
        return history[-limit:]

    def _timestamp_to_int(self, timestamp) -> int:
        if isinstance(timestamp, str):
            try:
                dt = datetime.strptime(timestamp, "%Y-%m-%d %H:%M:%S")
                return int(dt.timestamp())
            except ValueError:
                return 0
        return timestamp

    def add_alert(self, alert: Dict):
        alerts = self._load_json(self.alerts_file) or []
        target = alert.get("target", "")
        event_id = alert.get("event_id")

        existing_alert = None
        for a in alerts:
            if (a.get("target") == target and
                not a.get("handled", False) and
                a.get("event_id") == event_id):
                existing_alert = a
                break

        if existing_alert:
            existing_alert["last_timestamp"] = alert["timestamp"]
            existing_alert["last_message"] = alert["message"]
            existing_alert["last_response_time"] = alert["response_time"]
            existing_alert["last_level"] = alert["level"]
            existing_alert["count"] = existing_alert.get("count", 1) + 1
            existing_alert["consecutive_failures"] = alert.get("consecutive_failures", existing_alert.get("consecutive_failures", 1))
            if alert.get("level") == "critical":
                existing_alert["has_critical"] = True
            self._save_json(self.alerts_file, alerts)
            return existing_alert

        alert["handled"] = False
        alert["handler"] = None
        alert["conclusion"] = None
        alert["recovery_time"] = None
        alert["event_id"] = event_id
        alert["first_timestamp"] = alert["timestamp"]
        alert["last_timestamp"] = alert["timestamp"]
        alert["first_message"] = alert["message"]
        alert["last_message"] = alert["message"]
        alert["first_response_time"] = alert["response_time"]
        alert["last_response_time"] = alert["response_time"]
        alert["first_level"] = alert["level"]
        alert["last_level"] = alert["level"]
        alert["count"] = 1
        alert["has_critical"] = alert.get("level") == "critical"
        alerts.append(alert)
        self._save_json(self.alerts_file, alerts)
        return alert

    def get_alerts(self, target_name: Optional[str] = None,
                   only_unhandled: bool = False,
                   only_muted: bool = False,
                   level: Optional[str] = None,
                   group: Optional[str] = None) -> List[Dict]:
        alerts = self._load_json(self.alerts_file) or []
        if target_name:
            alerts = [a for a in alerts if a.get("target") == target_name]
        if only_unhandled:
            alerts = [a for a in alerts if not a.get("handled", False)]
        if only_muted:
            alerts = [a for a in alerts if self.is_muted(a.get("target", ""))]
        if level:
            alerts = [a for a in alerts if a.get("level") == level]
        if group:
            alerts = [a for a in alerts if self.get_target_group(a.get("target", "")) == group]
        return alerts

    def mark_alert_handled(self, alert_id: str, note: str = "",
                           handler: str = "", conclusion: str = "",
                           recovery_time = None) -> Tuple[bool, str]:
        alerts = self._load_json(self.alerts_file) or []
        if isinstance(recovery_time, str):
            try:
                dt = datetime.strptime(recovery_time, "%Y-%m-%d %H:%M:%S")
                recovery_time = int(dt.timestamp())
            except ValueError:
                return False, "恢复时间格式错误，应为 YYYY-MM-DD HH:MM:SS"

        for alert in alerts:
            if alert.get("id") == alert_id:
                event_id = alert.get("event_id")
                if event_id:
                    success, msg = self._close_event(
                        event_id, note, handler, conclusion, recovery_time, recovery_method="manual"
                    )
                    if not success:
                        return False, msg

                alert["handled"] = True
                alert["handled_note"] = note
                alert["handled_at"] = int(time.time())
                alert["handler"] = handler
                alert["conclusion"] = conclusion
                alert["recovery_time"] = recovery_time or int(time.time())
                self._save_json(self.alerts_file, alerts)

                return True, ""
        return False, "告警不存在"

    def mark_target_alerts_handled(self, target_name: str, note: str = "",
                                   handler: str = "", conclusion: str = "",
                                   recovery_time = None) -> Tuple[int, str]:
        alerts = self._load_json(self.alerts_file) or []
        if isinstance(recovery_time, str):
            try:
                dt = datetime.strptime(recovery_time, "%Y-%m-%d %H:%M:%S")
                recovery_time = int(dt.timestamp())
            except ValueError:
                return 0, "恢复时间格式错误，应为 YYYY-MM-DD HH:MM:SS"

        count = 0
        now = int(time.time())
        event_ids = set()
        for alert in alerts:
            if alert.get("target") == target_name and not alert.get("handled", False):
                if alert.get("event_id"):
                    event_ids.add(alert["event_id"])

        for event_id in event_ids:
            success, msg = self._close_event(
                event_id, note, handler, conclusion, recovery_time, recovery_method="manual"
            )
            if not success:
                return 0, msg

        for alert in alerts:
            if alert.get("target") == target_name and not alert.get("handled", False):
                alert["handled"] = True
                alert["handled_note"] = note
                alert["handled_at"] = now
                alert["handler"] = handler
                alert["conclusion"] = conclusion
                alert["recovery_time"] = recovery_time or now
                count += 1
        self._save_json(self.alerts_file, alerts)

        return count, ""

    def get_active_event(self, target: str) -> Optional[Dict]:
        state = self.load_state()
        event_id = state["active_events"].get(target)
        if not event_id:
            return None
        events = self._load_json(self.events_file) or []
        for event in events:
            if event.get("id") == event_id and not event.get("closed", False):
                return event
        if event_id in state["active_events"]:
            del state["active_events"][event_id]
            self.save_state(state)
        return None

    def create_or_update_event(self, target: str, level: str, message: str,
                               first_alert: Dict, response_time: float = 0.0) -> Dict:
        state = self.load_state()
        event_id = state["active_events"].get(target)
        events = self._load_json(self.events_file) or []
        now = int(time.time())

        if event_id:
            for event in events:
                if event.get("id") == event_id and not event.get("closed", False):
                    event["last_update"] = now
                    event["last_level"] = level
                    event["last_message"] = message
                    event["alert_count"] = event.get("alert_count", 0) + 1
                    if level == "critical":
                        event["has_critical"] = True

                    if "timeline" not in event:
                        event["timeline"] = [{
                            "type": "start",
                            "timestamp": event["start_time"],
                            "level": event["first_level"],
                            "message": event["first_message"],
                            "response_time": first_alert.get("response_time", 0)
                        }]

                    prev_level = event["timeline"][-1]["level"] if event["timeline"] else event["first_level"]
                    timeline_entry = {
                        "type": "level_change" if prev_level != level else "update",
                        "timestamp": now,
                        "level": level,
                        "message": message,
                        "response_time": response_time
                    }
                    event["timeline"].append(timeline_entry)

                    self._save_json(self.events_file, events)
                    return event

        target_events = [e for e in events if e.get("target") == target]
        target_events.sort(key=lambda x: x.get("start_time", 0), reverse=True)
        is_new_round = False
        previous_event_id = None
        if target_events:
            last_event = target_events[0]
            if last_event.get("closed", False) and last_event.get("recovery_method") == "manual":
                is_new_round = True
                previous_event_id = last_event.get("id")

        import uuid
        event_id = str(uuid.uuid4())
        start_time = first_alert.get("timestamp", now)
        if isinstance(start_time, str):
            try:
                dt = datetime.strptime(start_time, "%Y-%m-%d %H:%M:%S")
                start_time = int(dt.timestamp())
            except ValueError:
                start_time = now
        event = {
            "id": event_id,
            "target": target,
            "first_alert_id": first_alert.get("id"),
            "start_time": start_time,
            "last_update": now,
            "first_level": level,
            "last_level": level,
            "first_message": message,
            "last_message": message,
            "alert_count": 1,
            "has_critical": level == "critical",
            "closed": False,
            "close_time": None,
            "close_note": None,
            "close_handler": None,
            "close_conclusion": None,
            "recovery_time": None,
            "duration_seconds": None,
            "is_new_round": is_new_round,
            "previous_event_id": previous_event_id,
            "timeline": [{
                "type": "start",
                "timestamp": start_time,
                "level": level,
                "message": message,
                "response_time": response_time,
                "is_new_round": is_new_round
            }]
        }
        events.append(event)
        self._save_json(self.events_file, events)

        state["active_events"][target] = event_id
        self.save_state(state)

        return event

    def _close_event(self, event_id: str, note: str = "", handler: str = "",
                     conclusion: str = "", recovery_time: Optional[int] = None,
                     recovery_method: str = "manual") -> Tuple[bool, str]:
        events = self._load_json(self.events_file) or []
        now = int(time.time())
        for event in events:
            if event.get("id") == event_id and not event.get("closed", False):
                rt = recovery_time or now
                start_time = event["start_time"]
                if isinstance(start_time, str):
                    try:
                        dt = datetime.strptime(start_time, "%Y-%m-%d %H:%M:%S")
                        start_time = int(dt.timestamp())
                    except ValueError:
                        start_time = now

                if rt < start_time:
                    return False, "恢复时间不能早于事件开始时间"

                event["closed"] = True
                event["close_time"] = now
                event["close_note"] = note
                event["close_handler"] = handler
                event["close_conclusion"] = conclusion
                event["recovery_time"] = rt
                event["recovery_method"] = recovery_method
                event["duration_seconds"] = rt - start_time

                if "timeline" not in event:
                    event["timeline"] = [{
                        "type": "start",
                        "timestamp": event["start_time"],
                        "level": event["first_level"],
                        "message": event["first_message"],
                        "response_time": 0
                    }]
                event["timeline"].append({
                    "type": "recovery",
                    "timestamp": rt,
                    "method": recovery_method,
                    "handler": handler,
                    "conclusion": conclusion,
                    "note": note
                })

                self._save_json(self.events_file, events)

                state = self.load_state()
                target = event.get("target", "")
                if state["active_events"].get(target) == event_id:
                    del state["active_events"][target]
                self.save_state(state)
                return True, ""
        return False, "事件不存在或已关闭"

    def get_events(self, target: Optional[str] = None,
                   group: Optional[str] = None,
                   only_active: bool = False,
                   start_time: Optional[datetime] = None,
                   end_time: Optional[datetime] = None,
                   limit: int = 100,
                   impact_window: bool = False) -> List[Dict]:
        events = self._load_json(self.events_file) or []
        if target:
            events = [e for e in events if e.get("target") == target]
        if group:
            events = [e for e in events if self.get_target_group(e.get("target", "")) == group]
        if only_active:
            events = [e for e in events if not e.get("closed", False)]
        if start_time or end_time:
            if impact_window:
                start_ts = int(start_time.timestamp()) if start_time else 0
                end_ts = int(end_time.timestamp()) if end_time else int(time.time())
                filtered = []
                for event in events:
                    event_start = self._timestamp_to_int(event.get("start_time", 0))
                    event_end = self._timestamp_to_int(event.get("recovery_time", event.get("close_time", int(time.time()))))
                    if event.get("closed", False):
                        if event.get("recovery_time"):
                            event_end = self._timestamp_to_int(event["recovery_time"])
                        elif event.get("close_time"):
                            event_end = self._timestamp_to_int(event["close_time"])
                    else:
                        event_end = int(time.time())
                    if event_start <= end_ts and event_end >= start_ts:
                        filtered.append(event)
                events = filtered
            else:
                if start_time:
                    start_ts = int(start_time.timestamp())
                    events = [e for e in events if self._timestamp_to_int(e.get("start_time", 0)) >= start_ts]
                if end_time:
                    end_ts = int(end_time.timestamp())
                    events = [e for e in events if self._timestamp_to_int(e.get("start_time", 0)) <= end_ts]
        events.sort(key=lambda x: x.get("start_time", 0), reverse=True)
        return events[:limit]

    def check_recovery(self, target: str) -> Optional[Dict]:
        active_event = self.get_active_event(target)
        if active_event:
            return active_event
        return None

    def clear_active_event(self, target: str) -> None:
        state = self.load_state()
        if target in state["active_events"]:
            del state["active_events"][target]
            self.save_state(state)

    def close_event_on_recovery(self, target: str, recovery_time: Optional[int] = None) -> Optional[Dict]:
        active_event = self.get_active_event(target)
        if not active_event:
            return None

        event_id = active_event["id"]
        success, msg = self._close_event(
            event_id,
            note="自动恢复",
            handler="system",
            conclusion="resolved",
            recovery_time=recovery_time,
            recovery_method="auto"
        )
        if not success:
            return None

        alerts = self._load_json(self.alerts_file) or []
        for alert in alerts:
            if (alert.get("target") == target and
                not alert.get("handled", False) and
                alert.get("event_id") == event_id):
                alert["handled"] = True
                alert["handled_note"] = "自动恢复"
                alert["handled_at"] = int(time.time())
                alert["handler"] = "system"
                alert["conclusion"] = "resolved"
                alert["recovery_time"] = recovery_time or int(time.time())
        self._save_json(self.alerts_file, alerts)

        return active_event

    def add_event_note(self, event_id: str, note: str, author: str = "",
                       category: str = "") -> Tuple[bool, str]:
        events = self._load_json(self.events_file) or []
        now = int(time.time())
        for event in events:
            if event.get("id") == event_id:
                if "timeline" not in event:
                    event["timeline"] = [{
                        "type": "start",
                        "timestamp": event.get("start_time", now),
                        "level": event.get("first_level", ""),
                        "message": event.get("first_message", ""),
                        "response_time": 0
                    }]
                event["timeline"].append({
                    "type": "note",
                    "timestamp": now,
                    "note": note,
                    "author": author,
                    "category": category
                })
                event["last_update"] = now
                self._save_json(self.events_file, events)
                return True, ""
        return False, "事件不存在"
