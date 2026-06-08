import json
import os
from pathlib import Path
from typing import Dict, List, Optional, Any

DEFAULT_CONFIG_DIR = Path.home() / ".opsmonitor"
DEFAULT_CONFIG_FILE = DEFAULT_CONFIG_DIR / "config.json"
DEFAULT_HISTORY_FILE = DEFAULT_CONFIG_DIR / "history.json"
DEFAULT_ALERTS_FILE = DEFAULT_CONFIG_DIR / "alerts.json"

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


class ConfigManager:
    def __init__(self, config_dir: Optional[Path] = None):
        self.config_dir = config_dir or DEFAULT_CONFIG_DIR
        self.config_file = self.config_dir / "config.json"
        self.history_file = self.config_dir / "history.json"
        self.alerts_file = self.config_dir / "alerts.json"
        self._ensure_dir()

    def _ensure_dir(self):
        self.config_dir.mkdir(parents=True, exist_ok=True)
        if not self.config_file.exists():
            self.save_config(DEFAULT_CONFIG)
        if not self.history_file.exists():
            self._save_json(self.history_file, [])
        if not self.alerts_file.exists():
            self._save_json(self.alerts_file, [])

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
        self.save_config(config)
        return True

    def update_thresholds(self, **kwargs) -> None:
        config = self.load_config()
        for key, value in kwargs.items():
            if key in config["thresholds"]:
                config["thresholds"][key] = value
        self.save_config(config)

    def update_settings(self, **kwargs) -> None:
        config = self.load_config()
        for key, value in kwargs.items():
            if key in config["settings"]:
                config["settings"][key] = value
        self.save_config(config)

    def mute_target(self, name: str, duration_minutes: int = 60, reason: str = "") -> bool:
        config = self.load_config()
        if name not in config["targets"]:
            return False
        import time
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
        import time
        if config["muted"][name]["until"] < int(time.time()):
            del config["muted"][name]
            self.save_config(config)
            return False
        return True

    def get_targets_by_group(self) -> Dict[str, List[str]]:
        config = self.load_config()
        return config["groups"]

    def add_history_entry(self, entry: Dict):
        history = self._load_json(self.history_file) or []
        history.append(entry)
        if len(history) > 10000:
            history = history[-10000:]
        self._save_json(self.history_file, history)

    def get_history(self, target_name: Optional[str] = None,
                    limit: int = 100, only_errors: bool = False) -> List[Dict]:
        history = self._load_json(self.history_file) or []
        if target_name:
            history = [h for h in history if h.get("target") == target_name]
        if only_errors:
            history = [h for h in history if not h.get("success", True)]
        return history[-limit:]

    def add_alert(self, alert: Dict):
        alerts = self._load_json(self.alerts_file) or []
        alert["handled"] = False
        alerts.append(alert)
        self._save_json(self.alerts_file, alerts)

    def get_alerts(self, target_name: Optional[str] = None,
                   only_unhandled: bool = False) -> List[Dict]:
        alerts = self._load_json(self.alerts_file) or []
        if target_name:
            alerts = [a for a in alerts if a.get("target") == target_name]
        if only_unhandled:
            alerts = [a for a in alerts if not a.get("handled", False)]
        return alerts

    def mark_alert_handled(self, alert_id: str, note: str = "") -> bool:
        alerts = self._load_json(self.alerts_file) or []
        for alert in alerts:
            if alert.get("id") == alert_id:
                alert["handled"] = True
                alert["handled_note"] = note
                alert["handled_at"] = int(__import__("time").time())
                self._save_json(self.alerts_file, alerts)
                return True
        return False

    def mark_target_alerts_handled(self, target_name: str, note: str = "") -> int:
        alerts = self._load_json(self.alerts_file) or []
        count = 0
        import time
        for alert in alerts:
            if alert.get("target") == target_name and not alert.get("handled", False):
                alert["handled"] = True
                alert["handled_note"] = note
                alert["handled_at"] = int(time.time())
                count += 1
        self._save_json(self.alerts_file, alerts)
        return count
