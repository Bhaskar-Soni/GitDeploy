"""Dynamic port allocation and proxy management for running apps."""

import logging
import random
import re
import socket
import time
from typing import Optional

import docker
import requests
from docker.errors import NotFound

from core.config import settings

logger = logging.getLogger(__name__)


class ProxyManager:
    """Manages dynamic port allocation for exposing running app containers."""

    # Port range for dynamically allocated app ports
    PORT_RANGE_START = 10000
    PORT_RANGE_END = 11000

    @classmethod
    def allocate_port(cls) -> int:
        """Find an available port on the host in the dynamic range.

        Uses a randomized approach to avoid sequential collisions when
        multiple jobs start simultaneously. Checks both Docker container
        port mappings AND actual host socket availability.
        """
        used_ports = cls._get_docker_used_ports()
        # Build list of candidate ports and shuffle to avoid collisions
        candidates = [
            p for p in range(cls.PORT_RANGE_START, cls.PORT_RANGE_END)
            if p not in used_ports
        ]
        random.shuffle(candidates)

        for port in candidates:
            if cls._is_port_free(port):
                return port
        raise RuntimeError("No available ports in dynamic range")

    @staticmethod
    def _get_docker_used_ports() -> set:
        """Get all host ports currently mapped by Docker containers."""
        used = set()
        try:
            client = docker.from_env()
            for container in client.containers.list(all=True):
                ports = container.attrs.get("NetworkSettings", {}).get("Ports") or {}
                for _container_port, bindings in ports.items():
                    if bindings:
                        for b in bindings:
                            try:
                                used.add(int(b["HostPort"]))
                            except (KeyError, ValueError, TypeError):
                                pass
        except Exception:
            pass
        return used

    @staticmethod
    def _is_port_free(port: int) -> bool:
        """Check if a port is actually free on the host.

        When running inside Docker, socket.bind() only checks the container
        network namespace. To reliably verify host port availability, we also
        try to connect to the port — if something is listening, it's in use.
        """
        # First: check if something is already listening on the host
        # (works even from inside a container via host.docker.internal / gateway)
        try:
            host_addr = "host.docker.internal"
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(0.5)
                result = s.connect_ex((host_addr, port))
                if result == 0:
                    # Port is in use on the host
                    return False
        except (socket.gaierror, OSError):
            # host.docker.internal not available — fall back to bind test
            pass

        # Fallback: bind test (works when running directly on host)
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                s.bind(("0.0.0.0", port))
                return True
        except OSError:
            return False

    @staticmethod
    def wait_for_app(host: str, port: int, timeout: int = 60, container=None, internal_port: int = None) -> bool:
        """Wait for the app to start accepting connections.

        Polls every 2 seconds until the app responds or timeout is reached.
        Accepts any HTTP response (even 4xx/5xx) as proof the app is running.

        Tries multiple connectivity paths:
        1. Host port via provided host address (e.g., host.docker.internal)
        2. Container's internal port directly (if container object provided)
        3. Docker gateway IP
        """
        deadline = time.time() + timeout

        # Build list of (host, port) targets to try
        targets = [(host, port)]

        # Also try the Docker bridge gateway (common: 172.17.0.1 or 172.18.0.1)
        try:
            import subprocess
            gw = subprocess.check_output(
                ["sh", "-c", "ip route | grep default | awk '{print $3}'"],
                timeout=3,
            ).decode().strip()
            if gw:
                targets.append((gw, port))
        except Exception:
            pass

        # If we have the container object, try reaching it directly on its IP
        if container and internal_port:
            try:
                container.reload()
                networks = container.attrs.get("NetworkSettings", {}).get("Networks", {})
                for _net_info in networks.values():
                    ip = _net_info.get("IPAddress")
                    if ip:
                        targets.append((ip, internal_port))
                        break
            except Exception:
                pass

        while time.time() < deadline:
            for t_host, t_port in targets:
                try:
                    resp = requests.get(f"http://{t_host}:{t_port}/", timeout=3, allow_redirects=True)
                    return True
                except (requests.ConnectionError, requests.Timeout):
                    pass
                except requests.RequestException:
                    # Got a response (even if error) — app is running
                    return True

            # Check if container has exited (no point waiting)
            if container:
                try:
                    container.reload()
                    if container.status not in ("running", "created"):
                        return False
                except Exception:
                    pass

            time.sleep(2)
        return False

    @staticmethod
    def detect_listening_port(container, exclude_ports: set = None) -> Optional[int]:
        """Detect which port the app is actually listening on inside a container.

        Uses /proc/net/tcp (always available in Linux containers, no extra tools needed)
        then falls back to ss/netstat. Filters out known DB ports and ephemeral ports.

        Returns the detected port, or None if nothing found.
        """
        if exclude_ports is None:
            exclude_ports = set()

        # Standard DB/infra ports to ignore
        _infra_ports = {5432, 3306, 27017, 6379, 5672, 9092, 9200, 2181}
        skip_ports = _infra_ports | exclude_ports
        _web_ports = {80, 443, 3000, 3001, 4000, 4200, 5000, 5173, 8000, 8080, 8081, 8888, 9000}

        found_ports = []

        # Method 1: /proc/net/tcp — always available, no tools needed
        # Format: sl local_address rem_address st ...
        # local_address is hex IP:PORT, st=0A means LISTEN
        for proc_file in ["/proc/net/tcp", "/proc/net/tcp6"]:
            try:
                exec_result = container.exec_run(
                    ["cat", proc_file], demux=False
                )
                output = (exec_result.output or b"").decode("utf-8", errors="replace")
                for line in output.strip().split("\n")[1:]:  # skip header
                    fields = line.split()
                    if len(fields) < 4:
                        continue
                    state = fields[3]
                    if state != "0A":  # 0A = LISTEN
                        continue
                    local_addr = fields[1]
                    port_hex = local_addr.split(":")[1]
                    p = int(port_hex, 16)
                    if p not in skip_ports and 1024 < p < 65535:
                        found_ports.append(p)
            except Exception as e:
                logger.debug("detect_listening_port /proc/net/tcp failed: %s", e)

        if found_ports:
            for p in found_ports:
                if p in _web_ports:
                    return p
            return found_ports[0]

        # Method 2: ss/netstat fallback
        try:
            exec_result = container.exec_run(
                ["sh", "-c", "ss -tlnp 2>/dev/null | grep LISTEN || netstat -tlnp 2>/dev/null | grep LISTEN || true"],
                demux=False,
            )
            output = (exec_result.output or b"").decode("utf-8", errors="replace")
            if output.strip():
                for line in output.strip().split("\n"):
                    matches = re.findall(r'(?:0\.0\.0\.0|::|\*):(\d+)', line)
                    for m in matches:
                        p = int(m)
                        if p not in skip_ports and 1024 < p < 65535:
                            found_ports.append(p)
                if found_ports:
                    for p in found_ports:
                        if p in _web_ports:
                            return p
                    return found_ports[0]
        except Exception as e:
            logger.debug("detect_listening_port ss/netstat failed: %s", e)

        return None

    @staticmethod
    def get_proxy_url(port: int) -> str:
        """Generate the user-facing URL for a proxied app."""
        return f"http://localhost:{port}"
