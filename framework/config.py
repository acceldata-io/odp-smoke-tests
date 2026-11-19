"""Run settings, read from environment variables and (optionally) a KEY=value env file.

Secrets are passed to the executor node in a 0600 env file (path in SMOKE_ENV_FILE) rather than on
command lines, so they never show up in `ps` or Jenkins console output. Values in the file win over
the process environment.
"""
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Tuple


def _read_env_file(path: Optional[str]) -> dict:
    values = {}
    if not path or not Path(path).is_file():
        return values
    for line in Path(path).read_text().splitlines():
        if not line.strip() or line.lstrip().startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value
    return values


def _split(value: str) -> Tuple[str, ...]:
    parts = value.replace("\n", ",").replace(" ", ",").split(",")
    return tuple(p.strip() for p in parts if p.strip())


@dataclass(frozen=True)
class Settings:
    ips: Tuple[str, ...]          # cluster node IPs; ips[0] = Ambari server = executor
    ssh_user: str
    ssh_port: int
    ssh_key: str
    ambari_url: str               # empty -> auto-detect
    ambari_user: str
    ambari_password: str = field(repr=False)
    ranger_user: str
    ranger_password: str = field(repr=False)
    services: Tuple[str, ...]     # Ambari service names to test; empty = all installed
    tier: str                     # basic | medium
    report_dir: Path
    run_id: str
    ca_bundle: str                # PEM bundle for HTTPS verification; empty = system CAs

    @property
    def tls_verify(self):
        return self.ca_bundle or True


def load() -> Settings:
    env = dict(os.environ)
    env.update(_read_env_file(os.environ.get("SMOKE_ENV_FILE")))
    report_dir = Path(env.get("SMOKE_REPORT_DIR", "reports"))
    report_dir.mkdir(parents=True, exist_ok=True)
    return Settings(
        ips=_split(env.get("SMOKE_IPS", "")),
        ssh_user=env.get("SMOKE_SSH_USER", "acceldata"),
        ssh_port=int(env.get("SMOKE_SSH_PORT", "22") or 22),
        ssh_key=env.get("SMOKE_SSH_KEY", ""),
        ambari_url=env.get("AMBARI_URL", "").strip(),
        ambari_user=env.get("AMBARI_USER", "admin"),
        ambari_password=env.get("AMBARI_PASSWORD", ""),
        ranger_user=env.get("RANGER_USER", "admin"),
        ranger_password=env.get("RANGER_PASSWORD", ""),
        services=tuple(s.upper() for s in _split(env.get("SMOKE_SERVICES", ""))),
        tier=env.get("SMOKE_TIER", "basic").strip().lower() or "basic",
        report_dir=report_dir,
        run_id=env.get("SMOKE_RUN_ID", "local"),
        ca_bundle=env.get("SMOKE_CA_BUNDLE", ""),
    )
