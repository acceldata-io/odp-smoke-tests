"""Layer 0 health (STD-HL): hosts heartbeating, components started, no CRITICAL alerts — per service."""
import pytest

pytestmark = [pytest.mark.tier_basic, pytest.mark.dim_hl]


def test_hosts_heartbeating(discovery):
    bad = [f'{h["host_name"]}: {h.get("host_state")}' for h in discovery["hosts"] if h.get("host_state") != "HEALTHY"]
    if bad:
        pytest.fail("hosts not HEALTHY in Ambari: " + "; ".join(bad), pytrace=False)


def test_components_started(discovery, service):
    category = {c["component_name"]: c.get("category") for c in discovery["components"]}
    rows = [hc for hc in discovery["host_components"]
            if hc.get("service_name") == service and category.get(hc["component_name"]) != "CLIENT"]
    if not rows:
        pytest.skip(f"{service} has only client components")
    bad = [f'{r["component_name"]}@{r["host_name"]}={r["state"]}' for r in rows
           if r["state"] != "STARTED" and r.get("maintenance_state") != "ON"]
    if bad:
        pytest.fail(f"{service} components not STARTED: " + ", ".join(bad), pytrace=False)


def test_no_critical_alerts(discovery, service):
    alerts = [a for a in discovery["alerts"]["CRITICAL"]
              if a.get("service_name") == service and a.get("maintenance_state") != "ON"]
    msgs = [f'{a["definition_name"]}@{a.get("host_name") or "-"}: {_first_line(a.get("text"))}' for a in alerts]
    if msgs:
        pytest.fail(f"{service} CRITICAL alerts:\n  " + "\n  ".join(msgs), pytrace=False)


def _first_line(text, limit=200):
    """Alert texts often embed a Python traceback; the first line carries the actual reason."""
    line = (text or "").strip().splitlines()[0] if (text or "").strip() else ""
    return line.split(" (Traceback")[0][:limit]
