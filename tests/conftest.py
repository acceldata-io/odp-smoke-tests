import json

import pytest

from framework import config as config_mod
from framework import runtime
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
