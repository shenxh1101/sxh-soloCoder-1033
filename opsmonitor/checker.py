import time
import socket
import uuid
import subprocess
import platform
from typing import Dict, Optional, Tuple
from dataclasses import dataclass, field

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False


@dataclass
class CheckResult:
    target: str
    success: bool
    response_time: float
    status_code: Optional[int] = None
    error: Optional[str] = None
    timestamp: int = field(default_factory=lambda: int(time.time()))
    details: Dict = field(default_factory=dict)

    def to_dict(self) -> Dict:
        return {
            "target": self.target,
            "success": self.success,
            "response_time": self.response_time,
            "status_code": self.status_code,
            "error": self.error,
            "timestamp": self.timestamp,
            "details": self.details
        }

    def get_level(self, thresholds: Dict) -> str:
        if not self.success:
            return "critical"
        if self.response_time >= thresholds.get("response_time_critical", 2000):
            return "critical"
        if self.response_time >= thresholds.get("response_time_warning", 500):
            return "warning"
        return "ok"


class HealthChecker:
    def __init__(self, timeout: int = 10, retries: int = 2):
        self.timeout = timeout
        self.retries = retries

    def check(self, target_config: Dict, target_name: str) -> CheckResult:
        target_type = target_config.get("type", "http")
        check_func = {
            "http": self._check_http,
            "https": self._check_http,
            "tcp": self._check_tcp,
            "ping": self._check_ping,
            "icmp": self._check_ping
        }.get(target_type, self._check_http)

        last_result = None
        for attempt in range(self.retries + 1):
            try:
                result = check_func(target_config, target_name)
                last_result = result
                if result.success:
                    break
            except Exception as e:
                last_result = CheckResult(
                    target=target_name,
                    success=False,
                    response_time=0,
                    error=str(e)
                )
            time.sleep(0.5)

        return last_result

    def _check_http(self, config: Dict, target_name: str) -> CheckResult:
        if not HAS_REQUESTS:
            return CheckResult(
                target=target_name,
                success=False,
                response_time=0,
                error="requests library not installed"
            )

        address = config.get("address", "")
        method = config.get("method", "GET").upper()
        expected_status = config.get("expected_status", 200)
        port = config.get("port")

        url = address
        if not url.startswith(("http://", "https://")):
            url = f"http://{url}"
        if port and ":" not in url.split("://")[-1]:
            url = url.rstrip("/") + f":{port}"

        start_time = time.time()
        try:
            response = requests.request(
                method,
                url,
                timeout=self.timeout,
                allow_redirects=True
            )
            elapsed = (time.time() - start_time) * 1000

            success = response.status_code == expected_status
            return CheckResult(
                target=target_name,
                success=success,
                response_time=elapsed,
                status_code=response.status_code,
                error=None if success else f"Expected status {expected_status}, got {response.status_code}",
                details={
                    "url": url,
                    "method": method,
                    "headers": dict(response.headers)
                }
            )
        except requests.exceptions.Timeout:
            elapsed = (time.time() - start_time) * 1000
            return CheckResult(
                target=target_name,
                success=False,
                response_time=elapsed,
                error="Connection timeout"
            )
        except requests.exceptions.ConnectionError as e:
            elapsed = (time.time() - start_time) * 1000
            return CheckResult(
                target=target_name,
                success=False,
                response_time=elapsed,
                error=f"Connection failed: {str(e)}"
            )
        except Exception as e:
            elapsed = (time.time() - start_time) * 1000
            return CheckResult(
                target=target_name,
                success=False,
                response_time=elapsed,
                error=str(e)
            )

    def _check_tcp(self, config: Dict, target_name: str) -> CheckResult:
        address = config.get("address", "")
        port = config.get("port", 0)

        start_time = time.time()
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(self.timeout)
            sock.connect((address, port))
            elapsed = (time.time() - start_time) * 1000
            sock.close()
            return CheckResult(
                target=target_name,
                success=True,
                response_time=elapsed,
                details={"address": address, "port": port}
            )
        except socket.timeout:
            elapsed = (time.time() - start_time) * 1000
            return CheckResult(
                target=target_name,
                success=False,
                response_time=elapsed,
                error="Connection timeout"
            )
        except socket.error as e:
            elapsed = (time.time() - start_time) * 1000
            return CheckResult(
                target=target_name,
                success=False,
                response_time=elapsed,
                error=f"Connection failed: {str(e)}"
            )
        finally:
            try:
                sock.close()
            except:
                pass

    def _check_ping(self, config: Dict, target_name: str) -> CheckResult:
        address = config.get("address", "")
        param = "-n" if platform.system().lower() == "windows" else "-c"
        wait_param = "-w" if platform.system().lower() == "windows" else "-W"
        timeout_sec = str(int(self.timeout * 1000)) if platform.system().lower() == "windows" else str(self.timeout)

        start_time = time.time()
        try:
            result = subprocess.run(
                ["ping", param, "1", wait_param, timeout_sec, address],
                capture_output=True,
                text=True,
                timeout=self.timeout + 2
            )
            elapsed = (time.time() - start_time) * 1000

            success = result.returncode == 0
            output = result.stdout or result.stderr

            latency = None
            if success:
                if "time=" in output.lower():
                    import re
                    match = re.search(r"time=([\d.]+)", output, re.IGNORECASE)
                    if match:
                        latency = float(match.group(1))
                        elapsed = latency

            return CheckResult(
                target=target_name,
                success=success,
                response_time=elapsed,
                error=None if success else f"Ping failed: {output[:200]}",
                details={"address": address, "output": output[:500]}
            )
        except subprocess.TimeoutExpired:
            elapsed = (time.time() - start_time) * 1000
            return CheckResult(
                target=target_name,
                success=False,
                response_time=elapsed,
                error="Ping timeout"
            )
        except Exception as e:
            elapsed = (time.time() - start_time) * 1000
            return CheckResult(
                target=target_name,
                success=False,
                response_time=elapsed,
                error=str(e)
            )


class AlertManager:
    def __init__(self, config_manager):
        self.config_manager = config_manager
        self.consecutive_failures = {}

    def check_alert(self, result: CheckResult, thresholds: Dict) -> Optional[Dict]:
        target = result.target

        if self.config_manager.is_muted(target):
            return None

        if not result.success:
            self.consecutive_failures[target] = self.consecutive_failures.get(target, 0) + 1
        else:
            self.consecutive_failures[target] = 0

        level = result.get_level(thresholds)
        should_alert = False
        alert_type = ""

        if not result.success:
            if self.consecutive_failures[target] >= thresholds.get("consecutive_failures", 3):
                should_alert = True
                alert_type = "failure"
        elif level == "critical":
            should_alert = True
            alert_type = "slow_critical"
        elif level == "warning":
            should_alert = True
            alert_type = "slow_warning"

        if should_alert:
            alert = {
                "id": str(uuid.uuid4()),
                "target": target,
                "type": alert_type,
                "level": level,
                "message": result.error or f"Response time {result.response_time:.0f}ms exceeded threshold",
                "response_time": result.response_time,
                "timestamp": result.timestamp,
                "consecutive_failures": self.consecutive_failures.get(target, 0)
            }
            self.config_manager.add_alert(alert)
            return alert

        return None
