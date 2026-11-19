"""Run shell commands on cluster hosts over SSH (the `ssh` CLI, key-based, non-interactive)."""
import subprocess
from typing import Dict

from framework.config import Settings


def ssh(settings: Settings, ip: str, command: str, timeout: int = 120) -> subprocess.CompletedProcess:
    if not settings.ssh_key:
        raise RuntimeError("SMOKE_SSH_KEY is not set; cannot reach cluster hosts")
    argv = [
        "ssh", "-i", settings.ssh_key, "-p", str(settings.ssh_port),
        "-o", "BatchMode=yes",
        "-o", "StrictHostKeyChecking=no",
        "-o", "UserKnownHostsFile=/dev/null",
        "-o", "LogLevel=ERROR",
        "-o", "ConnectTimeout=15",
        f"{settings.ssh_user}@{ip}",
        command,
    ]
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout)


_hostnames: Dict[str, str] = {}


def hostname_of(settings: Settings, ip: str) -> str:
    """`hostname -f` of the node at `ip` ('' if unreachable). Cached per run."""
    if ip not in _hostnames:
        r = ssh(settings, ip, "hostname -f", timeout=30)
        _hostnames[ip] = r.stdout.strip().lower() if r.returncode == 0 else ""
    return _hostnames[ip]


def ip_for_ambari_host(settings: Settings, host: dict) -> str:
    """Map an Ambari host to one of the given IPs: by Ambari's reported IP, else by FQDN/short hostname."""
    if host.get("ip") in settings.ips:
        return host["ip"]
    name = host["host_name"].lower()
    for ip in settings.ips:
        fqdn = hostname_of(settings, ip)
        if fqdn and (fqdn == name or fqdn.split(".")[0] == name.split(".")[0]):
            return ip
    return ""
