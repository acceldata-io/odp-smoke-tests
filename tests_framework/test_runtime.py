"""Offline unit tests for runtime parsing (JDK 8/11/17/21, Python 2/3)."""
import pytest

from framework.runtime import foreign_jvms, parse_java_major, parse_probe


@pytest.mark.parametrize("line,major", [
    ('openjdk version "1.8.0_392"', 8),
    ('java version "1.8.0_202"', 8),
    ('openjdk version "11.0.21" 2023-10-17', 11),
    ('openjdk version "17.0.9" 2023-10-17', 17),
    ('openjdk version "21" 2023-09-19', 21),
    ('openjdk version "21.0.2" 2024-01-16 LTS', 21),
    ("MISSING", None),
])
def test_parse_java_major(line, major):
    assert parse_java_major(line) == major


PROBE_OUTPUT = """STACKJDK openjdk version "17.0.9" 2023-10-17
STACKJDKREAL /usr/lib/jvm/java-17-openjdk-17.0.9
PY python2 /usr/bin/python2 Python 2.7.18
PY python3 /usr/bin/python3 Python 3.11.7
PY python3.12 /usr/bin/python3.12 Python 3.12.1
AGENTPY Python 3.11.7
JVM 101 hdfs /usr/lib/jvm/java-17-openjdk-17.0.9/bin/java 0
JVM 102 kafka /usr/lib/jvm/java-21-openjdk/bin/java 0
JVM 103 root /usr/lib/jvm/java-21-openjdk/bin/java 1
"""


def test_parse_probe_and_foreign_jvms():
    f = parse_probe(PROBE_OUTPUT)
    assert f["stack_jdk_major"] == 17
    assert f["pythons"]["python2"]["version"] == "Python 2.7.18"
    assert f["ambari_agent_python"] == "Python 3.11.7"
    bad = foreign_jvms(f)
    assert [j["pid"] for j in bad] == ["102"]        # Ambari server (103) is exempt


def test_foreign_jvms_prefix_is_not_fooled():
    f = parse_probe("STACKJDKREAL /usr/lib/jvm/jdk17\nJVM 1 x /usr/lib/jvm/jdk17-other/bin/java 0\n")
    assert len(foreign_jvms(f)) == 1


def test_service_specific_jdks_are_allowed():
    out = PROBE_OUTPUT + "HOMEREAL /usr/lib/jvm/java-21-openjdk /usr/lib/jvm/java-21-openjdk-21.0.5\n"
    out = out.replace("/usr/lib/jvm/java-21-openjdk/bin/java", "/usr/lib/jvm/java-21-openjdk-21.0.5/bin/java")
    assert foreign_jvms(parse_probe(out)) == []      # NiFi-style JDK 21 configured in Ambari -> fine
