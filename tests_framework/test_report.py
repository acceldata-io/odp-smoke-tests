"""Offline unit tests for the summary report."""
from framework.report import Collector


def make():
    c = Collector()
    c.add("t::a[HDFS]", "test_components_started", {"service": "HDFS"}, "passed", "")
    c.add("t::b[HDFS]", "test_no_critical_alerts", {"service": "HDFS"}, "failed", "HDFS CRITICAL alerts:\n  x@h1: boom")
    c.add("t::a[KERBEROS]", "test_components_started", {"service": "KERBEROS"}, "skipped", "only client components")
    c.add("t::ssh[1.2.3.4]", "test_ssh_and_sudo", {"host_ip": "1.2.3.4"}, "passed", "")
    c.add("t::login", "test_ambari_login", {}, "error", "Ambari discovery failed: <bad>")
    return c


def test_text_matrix_and_problems():
    t = make().text("T")
    assert "passed 2  failed 1  errors 1  skipped 1" in t
    assert "HDFS" in t and "FAIL" in t and "PROBLEMS" in t and "x@h1: boom" in t
    assert "test_ssh_and_sudo [1.2.3.4]" in t


def test_html_is_escaped_and_has_no_script():
    h = make().html("T")
    assert "<script" not in h and "&lt;bad&gt;" in h and "<b>FAIL</b>" in h


def test_empty_collector_renders():
    c = Collector()
    c.add("t::x", "test_x", {}, "error", "boom")
    assert "test_x" in c.text("T") and "test_x" in c.html("T")
