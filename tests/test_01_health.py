"""Layer 0 health (STD-HL): hosts heartbeating, components started, no CRITICAL alerts — per service."""
import pytest

pytestmark = [pytest.mark.tier_basic, pytest.mark.dim_hl]


def test_hosts_heartbeating(discovery):
    bad = [f'{h["host_name"]}: {h.get("host_state")}' for h in discovery["hosts"] if h.get("host_state") != "HEALTHY"]
    assert not bad, "hosts not HEALTHY in Ambari: " + "; ".join(bad)


def test_components_started(discovery, service):
    category = {c["component_name"]: c.get("category") for c in discovery["components"]}
    rows = [hc for hc in discovery["host_components"]
            if hc.get("service_name") == service and category.get(hc["component_name"]) != "CLIENT"]
    if not rows:
        pytest.skip(f"{service} has only client components")
    bad = [f'{r["component_name"]}@{r["host_name"]}={r["state"]}' for r in rows
           if r["state"] != "STARTED" and r.get("maintenance_state") != "ON"]
    assert not bad, f"{service} components not STARTED: " + ", ".join(bad)


def test_no_critical_alerts(discovery, service):
    alerts = [a for a in discovery["alerts"]["CRITICAL"]
              if a.get("service_name") == service and a.get("maintenance_state") != "ON"]
    msgs = [f'{a["definition_name"]}@{a.get("host_name") or "-"}: {(a.get("text") or "")[:160]}' for a in alerts]
    assert not msgs, f"{service} CRITICAL alerts:\n  " + "\n  ".join(msgs)
