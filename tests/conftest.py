import json

import pytest

from framework import config as config_mod
from framework import runtime
from framework.report import Collector
from framework.discovery import discover, selected_services

_SETTINGS = None


def _settings():
    global _SETTINGS
    if _SETTINGS is None:
        _SETTINGS = config_mod.load()
    return _SETTINGS


@pytest.fixture(scope="session")
def settings():
    return _settings()


@pytest.fixture(scope="session")
def discovery(settings):
    data, error = discover(settings)
    if error is not None:
        pytest.fail(f"Ambari discovery failed: {error}", pytrace=False)
    return data


def pytest_generate_tests(metafunc):
    s = _settings()
    if "host_ip" in metafunc.fixturenames:
        metafunc.parametrize("host_ip", list(s.ips), ids=list(s.ips))
    if "service" in metafunc.fixturenames:
        data, _ = discover(s)
        names = selected_services(s, data)
        metafunc.parametrize("service", names, ids=names)


def pytest_collection_modifyitems(config, items):
    if _settings().tier != "basic":
        return
    keep, drop = [], []
    for item in items:
        (drop if item.get_closest_marker("tier_medium") else keep).append(item)
    if drop:
        config.hook.pytest_deselected(items=drop)
        items[:] = keep


def pytest_sessionfinish(session, exitstatus):
    s = _settings()
    data, error = discover(s)
    summary = {"run_id": s.run_id, "tier": s.tier, "ips": list(s.ips),
               "services_filter": list(s.services), "discovery_error": str(error) if error else None}
    if data:
        summary.update(ambari_url=data["ambari_url"], cluster=data["cluster"],
                       services=[x["service_name"] for x in data["services"]],
                       hosts=[{"host": h["host_name"], "ip": h.get("ip")} for h in data["hosts"]])
        cl = data["profile"]["cluster"]
        summary["topology"] = {"kerberos": cl["kerberos"], "ha": cl["ha"], "ha_components": cl["ha_components"]}
        (s.report_dir / "profile.json").write_text(json.dumps(data["profile"], indent=2, sort_keys=True))
    if runtime._cache is not None:  # only if the runtime tests ran
        summary.setdefault("topology", {})["runtime"] = runtime.summarize(runtime._cache)
    (s.report_dir / "run_summary.json").write_text(json.dumps(summary, indent=2))


# ---- summary report (reports/summary.txt + summary.html, printed at the end of the console) ----
_results = {}


def _message(rep):
    if rep.skipped and isinstance(rep.longrepr, tuple):
        return str(rep.longrepr[2]).replace("Skipped: ", "", 1)
    text = rep.longreprtext or ""
    e_lines = [l[1:].strip() for l in text.splitlines() if l.startswith("E ")]
    return "\n".join(e_lines) if e_lines else text


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    rep = outcome.get_result()
    raw = item.callspec.params if hasattr(item, "callspec") else {}
    # drop pytest's NOTSET placeholder (used when a parameter list is empty, e.g. Ambari unreachable)
    params = {k: v for k, v in raw.items() if isinstance(v, (str, int, float))}
    entry = _results.setdefault(item.nodeid, {"name": item.originalname or item.name, "params": params,
                                              "outcome": None, "message": ""})
    if rep.when == "setup" and (rep.failed or rep.skipped):
        entry.update(outcome="error" if rep.failed else "skipped", message=_message(rep))
    elif rep.when == "call":
        entry.update(outcome=rep.outcome, message=_message(rep) if not rep.passed else "")
    elif rep.when == "teardown" and rep.failed:
        entry.update(outcome="error", message=_message(rep))


def pytest_terminal_summary(terminalreporter):
    s = _settings()
    collector = Collector()
    for nodeid, r in _results.items():
        if r["outcome"]:
            collector.add(nodeid, r["name"], r["params"], r["outcome"], r["message"])
    if not collector.results:
        return
    data, _ = discover(s)
    cluster = data["cluster"]["name"] if data else (s.ips[0] if s.ips else "?")
    title = f"ODP smoke tests - cluster {cluster} - run {s.run_id} - tier {s.tier}"
    try:  # the summary must never break a test run
        text = collector.text(title)
        (s.report_dir / "summary.txt").write_text(text)
        (s.report_dir / "summary.html").write_text(collector.html(title))
    except Exception as e:
        terminalreporter.write_line(f"WARNING: could not build summary report: {e!r}")
        return
    terminalreporter.write_sep("=", "SUMMARY (also in reports/summary.txt / summary.html)")
    terminalreporter.write(text)
