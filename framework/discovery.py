"""One-shot discovery of the cluster through Ambari, cached for the whole pytest session."""
from typing import Optional, Tuple

from framework.ambari import Ambari, detect_ambari_url
from framework.config import Settings
from framework.profile import build_profile

_cache: Optional[Tuple[Optional[dict], Optional[Exception]]] = None


def discover(settings: Settings) -> Tuple[Optional[dict], Optional[Exception]]:
    """Returns (discovery, error). Never raises, so test collection still works when Ambari is down."""
    global _cache
    if _cache is not None:
        return _cache
    try:
        if not settings.ips:
            raise RuntimeError("SMOKE_IPS is empty (no cluster nodes given)")
        if not settings.ambari_password:
            raise RuntimeError("AMBARI_PASSWORD was not provided")
        url = detect_ambari_url(settings.ambari_url, settings.ips[0])
        ambari = Ambari(url, settings.ambari_user, settings.ambari_password, verify=settings.tls_verify)
        info = ambari.cluster_info()
        services = ambari.services()
        components = ambari.components()
        host_components = ambari.host_components()
        configs = ambari.current_configs(info.get("desired_configs", {}))
        names = [s["service_name"] for s in services]
        data = {
            "ambari_url": url,
            "cluster": {"name": ambari.cluster, "version": info.get("version"),
                        "security_type": info.get("security_type")},
            "services": services,
            "components": components,
            "host_components": host_components,
            "hosts": ambari.hosts(),
            "alerts": {"CRITICAL": ambari.alerts("CRITICAL"), "WARNING": ambari.alerts("WARNING")},
            "profile": build_profile(info, configs, names, components, host_components),
        }
        _cache = (data, None)
    except Exception as e:  # reported by the preflight tests
        _cache = (None, e)
    return _cache


def selected_services(settings: Settings, data: Optional[dict]):
    if not data:
        return []
    installed = [s["service_name"] for s in data["services"]]
    if not settings.services:
        return sorted(installed)
    return [s for s in settings.services if s in installed]
