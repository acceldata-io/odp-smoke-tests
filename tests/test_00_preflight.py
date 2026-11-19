"""Preflight: can we reach every node, log in to Ambari and Ranger, and see the whole cluster?"""
import pytest
import requests

from framework.remote import ip_for_ambari_host, ssh

pytestmark = [pytest.mark.tier_basic, pytest.mark.dim_pre]


def test_ssh_and_sudo(settings, host_ip):
    r = ssh(settings, host_ip, "sudo -n true && hostname -f")
    assert r.returncode == 0, f"ssh/sudo to {settings.ssh_user}@{host_ip} failed: {r.stderr.strip() or r.stdout.strip()}"


def test_ambari_login(discovery):
    assert discovery["cluster"]["name"]


def test_every_ambari_host_is_in_ip_list(settings, discovery):
    """Per-host checks (logs, policy caches) need SSH to every host, so every Ambari host must be in the IP list."""
    missing = [f'{h["host_name"]} ({h.get("ip")})' for h in discovery["hosts"] if not ip_for_ambari_host(settings, h)]
    assert not missing, f"Ambari hosts not in the given IP list: {', '.join(missing)}"


def test_ranger_login(settings, discovery):
    ranger = discovery["profile"]["services"].get("RANGER")
    if ranger is None:
        pytest.skip("RANGER is not installed")
    url = ranger["endpoints"].get("admin", {}).get("url")
    assert url, "could not determine Ranger Admin URL (admin-properties/policymgr_external_url)"
    if not settings.ranger_password:  # not an assert: pytest would print the Settings object
        pytest.fail("RANGER_PASSWORD was not provided", pytrace=False)
    try:
        r = requests.get(f"{url}/service/public/v2/api/service", auth=(settings.ranger_user, settings.ranger_password),
                         timeout=30, verify=settings.tls_verify)
    except requests.RequestException as e:
        pytest.fail(f"cannot reach Ranger Admin at {url}: {e}", pytrace=False)
    assert r.status_code == 200, f"Ranger Admin login failed at {url}: HTTP {r.status_code}"
