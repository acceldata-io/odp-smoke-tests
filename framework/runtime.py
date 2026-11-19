"""Per-host runtime facts collected over SSH: Python interpreters and the JDK each running JVM really uses.

Clusters vary: Python 2 / 3.11 / 3.12, JDK 8 / 11 / 17 / 21, often with several JDKs installed side by side.
Ambari's configured stack JDK is `stack.java.home` (falls back to `java.home`) in ambari.properties.
"""
import os
import re
import shlex
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, Iterable, List, Optional

from framework.ambari import read_ambari_properties
from framework.config import Settings
from framework.remote import ssh

_PYTHONS = "python2 python3 python3.8 python3.9 python3.10 python3.11 python3.12 python3.13"

_PROBE = r"""
J={java_home}
if [ -n "$J" ]; then
  if [ -x "$J/bin/java" ]; then echo "STACKJDK $("$J/bin/java" -version 2>&1 | head -1)"; else echo "STACKJDK MISSING"; fi
  echo "STACKJDKREAL $(readlink -f "$J")"
fi
for h in {extra_homes}; do echo "HOMEREAL $h $(readlink -f "$h" 2>/dev/null)"; done
for p in {pythons}; do
  x=$(command -v $p 2>/dev/null) && echo "PY $p $x $($x -V 2>&1 | head -1)"
done
[ -x /usr/bin/ambari-python-wrap ] && echo "AGENTPY $(/usr/bin/ambari-python-wrap -V 2>&1 | head -1)"
for pid in $(pgrep -x java 2>/dev/null); do
  exe=$(readlink -f /proc/$pid/exe 2>/dev/null) || continue
  user=$(stat -c %U /proc/$pid 2>/dev/null)
  amb=$(tr '\0' ' ' < /proc/$pid/cmdline 2>/dev/null | grep -c 'org.apache.ambari.server')
  echo "JVM $pid $user $exe $amb"
done
"""


def parse_java_major(version_line: str) -> Optional[int]:
    """'openjdk version "1.8.0_392"' -> 8, 'openjdk version "17.0.9" 2023-10-17' -> 17, '... "21" ...' -> 21."""
    m = re.search(r'version "(\d+)(?:\.(\d+))?', version_line or "")
    if not m:
        return None
    major = int(m.group(1))
    return int(m.group(2)) if major == 1 and m.group(2) else major


def parse_probe(output: str) -> dict:
    facts = {"stack_jdk": None, "stack_jdk_major": None, "stack_jdk_real": None, "configured_jdks_real": {},
             "pythons": {}, "ambari_agent_python": None, "jvms": []}
    for line in output.splitlines():
        tag, _, rest = line.partition(" ")
        if tag == "STACKJDK":
            facts["stack_jdk"] = rest
            facts["stack_jdk_major"] = parse_java_major(rest)
        elif tag == "STACKJDKREAL":
            facts["stack_jdk_real"] = rest or None
        elif tag == "HOMEREAL":
            configured, _, real = rest.partition(" ")
            if real:
                facts["configured_jdks_real"][configured] = real
        elif tag == "PY":
            name, path, version = (rest.split(" ", 2) + ["", ""])[:3]
            facts["pythons"][name] = {"path": path, "version": version}
        elif tag == "AGENTPY":
            facts["ambari_agent_python"] = rest
        elif tag == "JVM":
            parts = rest.split()
            if len(parts) >= 4:
                facts["jvms"].append({"pid": parts[0], "user": parts[1], "exe": parts[2],
                                      "ambari_server": parts[3] != "0"})
    return facts


def stack_java_home() -> str:
    """Configured stack JDK: env override, else ambari.properties (we normally run on the Ambari server)."""
    if os.environ.get("SMOKE_STACK_JAVA_HOME"):
        return os.environ["SMOKE_STACK_JAVA_HOME"]
    props = read_ambari_properties()
    return props.get("stack.java.home") or props.get("java.home") or ""


_cache: Optional[Dict[str, dict]] = None


def collect(settings: Settings, extra_java_homes: Iterable[str] = ()) -> Dict[str, dict]:
    """{ip: facts} for every node, probed in parallel. Unreachable nodes get {"error": ...}.

    extra_java_homes: service-specific JDK paths from Ambari configs (resolved per host via readlink -f).
    """
    global _cache
    if _cache is not None:
        return _cache
    java_home = stack_java_home()
    homes = " ".join(shlex.quote(h) for h in sorted(set(extra_java_homes)))
    script = (_PROBE.replace("{java_home}", shlex.quote(java_home)).replace("{pythons}", _PYTHONS)
              .replace("{extra_homes}", homes))

    def probe(ip):
        try:
            r = ssh(settings, ip, "sudo -n bash -c " + shlex.quote(script), timeout=60)
        except Exception as e:  # timeout etc.
            return ip, {"error": str(e)}
        if r.returncode != 0 and not r.stdout:
            return ip, {"error": (r.stderr or "probe failed").strip()[:300]}
        return ip, parse_probe(r.stdout)

    with ThreadPoolExecutor(max_workers=min(16, max(1, len(settings.ips)))) as pool:
        _cache = dict(pool.map(probe, settings.ips))
    return _cache


def summarize(facts: Dict[str, dict]) -> dict:
    majors = sorted({f.get("stack_jdk_major") for f in facts.values() if f.get("stack_jdk_major")})
    return {
        "stack_java_home": stack_java_home(),
        "stack_jdk_majors": majors,
        "pythons": {ip: sorted(v["version"] for v in f.get("pythons", {}).values()) for ip, f in facts.items()},
        "ambari_agent_python": {ip: f.get("ambari_agent_python") for ip, f in facts.items()},
    }


def foreign_jvms(facts: dict) -> List[dict]:
    """JVMs (other than Ambari server) running from a JDK that Ambari did not configure:
    neither the stack JDK nor any service-specific java_home."""
    allowed = [p.rstrip("/") + "/" for p in [facts.get("stack_jdk_real")] + list(facts.get("configured_jdks_real", {}).values()) if p]
    if not allowed:
        return []
    return [j for j in facts.get("jvms", [])
            if not j["ambari_server"] and not any(j["exe"].startswith(a) for a in allowed)]
