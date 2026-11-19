"""Runtime (STD-RT): the stack JDK exists everywhere and every JVM runs on a JDK that Ambari configured.

Works for JDK 8 / 11 / 17 / 21 and catches the common failure where several JDKs are installed and a
service silently starts on the wrong one.
"""
import pytest

from framework import runtime
from framework.discovery import discover

pytestmark = [pytest.mark.tier_basic, pytest.mark.dim_hl]


def configured_java_homes(settings):
    """Service-specific JDKs from Ambari configs; None when Ambari is not reachable."""
    data, error = discover(settings)
    if error is not None:
        return None
    return sorted({h for s in data["profile"]["services"].values() for h in s.get("java_homes", [])})


@pytest.fixture(scope="session")
def runtime_facts(settings):
    return runtime.collect(settings, configured_java_homes(settings) or [])


@pytest.fixture
def host_facts(runtime_facts, host_ip):
    facts = runtime_facts.get(host_ip, {})
    if "error" in facts:
        pytest.fail(f"runtime probe failed on {host_ip}: {facts['error']}", pytrace=False)
    if not runtime.stack_java_home():
        pytest.skip("stack JDK unknown (not on the Ambari server); set SMOKE_STACK_JAVA_HOME")
    return facts


def test_stack_jdk_installed(host_facts, host_ip):
    assert host_facts["stack_jdk"] not in (None, "MISSING"), \
        f"stack JDK {runtime.stack_java_home()} has no runnable bin/java on {host_ip}"
    assert host_facts["stack_jdk_major"], f"cannot parse java -version on {host_ip}: {host_facts['stack_jdk']}"


def test_same_jdk_major_on_all_hosts(runtime_facts):
    majors = {ip: f.get("stack_jdk_major") for ip, f in runtime_facts.items() if "error" not in f}
    if not runtime.stack_java_home():
        pytest.skip("stack JDK unknown (not on the Ambari server); set SMOKE_STACK_JAVA_HOME")
    assert len(set(majors.values())) == 1, f"stack JDK major version differs between hosts: {majors}"


def test_service_jvms_use_configured_jdk(settings, host_facts, host_ip):
    """Every JVM must run on the stack JDK or on a JDK configured for some service in Ambari
    (e.g. nifi2-env/nifi_java_home -> JDK 21, trino-env/java_home -> JDK 23)."""
    if configured_java_homes(settings) is None:
        pytest.skip("needs Ambari configs to know service-specific JDKs (AMBARI_PASSWORD)")
    bad = runtime.foreign_jvms(host_facts)
    msgs = [f'pid {j["pid"]} ({j["user"]}) runs {j["exe"]}' for j in bad]
    assert not msgs, (f"JVMs on {host_ip} on a JDK not configured in Ambari (stack JDK {host_facts['stack_jdk_real']}):\n  "
                      + "\n  ".join(msgs))
