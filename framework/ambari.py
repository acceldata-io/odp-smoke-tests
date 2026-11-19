"""Minimal Ambari REST client (read-only for now)."""
from pathlib import Path
from typing import Dict, List

import requests

AMBARI_PROPERTIES = Path("/etc/ambari-server/conf/ambari.properties")


class AmbariError(Exception):
    pass


def read_ambari_properties() -> Dict[str, str]:
    """ambari.properties of the local Ambari server ({} when not running on the Ambari server)."""
    props: Dict[str, str] = {}
    if AMBARI_PROPERTIES.is_file():
        for line in AMBARI_PROPERTIES.read_text(errors="replace").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                props[k.strip()] = v.strip()
    return props


def detect_ambari_url(explicit_url: str, ambari_host: str) -> str:
    """Explicit URL wins; else read ambari.properties (we normally run on the Ambari server); else http:8080."""
    if explicit_url:
        return explicit_url.rstrip("/")
    props = read_ambari_properties()
    if props:
        if props.get("api.ssl", "false").lower() == "true":
            return f"https://{ambari_host}:{props.get('client.api.ssl.port', '8443')}"
        return f"http://{ambari_host}:{props.get('client.api.port', '8080')}"
    return f"http://{ambari_host}:8080"


class Ambari:
    def __init__(self, url: str, user: str, password: str, verify=True, timeout: int = 60):
        self.url = url.rstrip("/")
        self.base = self.url + "/api/v1"
        self.timeout = timeout
        self.session = requests.Session()
        self.session.auth = (user, password)
        self.session.verify = verify
        self.session.headers["X-Requested-By"] = "odp-smoke-tests"
        self._cluster = None

    # Paths are passed with their query string already built: Ambari's `fields=` and predicates use
    # '/' and ',' which must not be percent-encoded.
    def get(self, path: str) -> dict:
        try:
            r = self.session.get(self.base + path, timeout=self.timeout)
        except requests.RequestException as e:
            raise AmbariError(f"cannot reach Ambari at {self.url}: {e}") from e
        if r.status_code in (401, 403):
            raise AmbariError(f"Ambari rejected the credentials (HTTP {r.status_code})")
        if not r.ok:
            raise AmbariError(f"GET {path} -> HTTP {r.status_code}: {r.text[:300]}")
        return r.json()

    @property
    def cluster(self) -> str:
        if self._cluster is None:
            items = self.get("/clusters").get("items", [])
            if not items:
                raise AmbariError("Ambari has no cluster")
            self._cluster = items[0]["Clusters"]["cluster_name"]
        return self._cluster

    def cluster_info(self) -> dict:
        return self.get(f"/clusters/{self.cluster}?fields=Clusters/security_type,Clusters/version,"
                        f"Clusters/desired_configs")["Clusters"]

    def services(self) -> List[dict]:
        data = self.get(f"/clusters/{self.cluster}/services?fields=ServiceInfo/state,ServiceInfo/maintenance_state")
        return [i["ServiceInfo"] for i in data.get("items", [])]

    def components(self) -> List[dict]:
        data = self.get(f"/clusters/{self.cluster}/components?fields=ServiceComponentInfo/category,"
                        f"ServiceComponentInfo/service_name")
        return [i["ServiceComponentInfo"] for i in data.get("items", [])]

    def host_components(self) -> List[dict]:
        data = self.get(f"/clusters/{self.cluster}/host_components?fields=HostRoles/component_name,"
                        f"HostRoles/host_name,HostRoles/state,HostRoles/maintenance_state,HostRoles/service_name")
        return [i["HostRoles"] for i in data.get("items", [])]

    def hosts(self) -> List[dict]:
        data = self.get(f"/clusters/{self.cluster}/hosts?fields=Hosts/host_name,Hosts/ip,Hosts/host_state,"
                        f"Hosts/host_status,Hosts/maintenance_state")
        return [i["Hosts"] for i in data.get("items", [])]

    def alerts(self, state: str) -> List[dict]:
        data = self.get(f"/clusters/{self.cluster}/alerts?Alert/state={state}&fields=Alert/definition_name,"
                        f"Alert/service_name,Alert/component_name,Alert/host_name,Alert/text,Alert/maintenance_state")
        return [i["Alert"] for i in data.get("items", [])]

    def current_configs(self, desired_configs: Dict[str, dict]) -> Dict[str, dict]:
        """{config_type: {"service": <name or None>, "properties": {...}}} for every current config type."""
        out: Dict[str, dict] = {}
        data = self.get(f"/clusters/{self.cluster}/configurations/service_config_versions?is_current=true")
        for item in data.get("items", []):
            for cfg in item.get("configurations", []):
                out[cfg["type"]] = {"service": item.get("service_name"), "properties": cfg.get("properties", {})}
        # Cluster-level types (cluster-env, …) are not tied to a service; fetch what is still missing.
        for ctype, meta in desired_configs.items():
            if ctype in out:
                continue
            items = self.get(f"/clusters/{self.cluster}/configurations?type={ctype}&tag={meta['tag']}").get("items", [])
            out[ctype] = {"service": None, "properties": items[0].get("properties", {}) if items else {}}
        return out
