"""ClusterProfile: how each service/endpoint is secured, derived from Ambari configs.

Design (docs/ODP-Smoke-Test-Config-Matrix.md): never ask "is the cluster secure?", ask
"how is THIS endpoint secured?". Tests read this profile instead of hard-coding Kerberos/TLS.
Only derived facts go into the profile — never raw config values (they can contain secrets).
"""
import re
from typing import Dict, List

TLS_POLICIES = {"HTTPS_ONLY", "HTTP_AND_HTTPS"}
JAVA_HOME_KEY = re.compile(r"java\d*[._]?home$", re.IGNORECASE)


def _props(configs: dict, ctype: str) -> dict:
    return configs.get(ctype, {}).get("properties", {}) or {}


def _truthy(value) -> bool:
    return str(value).strip().lower() in ("true", "yes", "1", "on")


def parse_kafka_listeners(listeners: str, protocol_map: str = "") -> List[dict]:
    """'SASL_PLAINTEXT://h:6667,CONTROLLER://:9093' + map -> [{name, protocol, port, tls, sasl}]."""
    pmap = {}
    for pair in (protocol_map or "").split(","):
        if ":" in pair:
            name, proto = pair.split(":", 1)
            pmap[name.strip().upper()] = proto.strip().upper()
    out = []
    for entry in (listeners or "").split(","):
        entry = entry.strip()
        if "://" not in entry:
            continue
        name, addr = entry.split("://", 1)
        name = name.strip().upper()
        proto = pmap.get(name, name)
        port = addr.rsplit(":", 1)[-1] if ":" in addr else ""
        out.append({"name": name, "protocol": proto, "port": port,
                    "tls": proto in ("SSL", "SASL_SSL"), "sasl": proto.startswith("SASL_")})
    return out


def _http_policy_endpoint(value) -> dict:
    policy = (value or "HTTP_ONLY").upper()
    return {"tls": policy in TLS_POLICIES, "plaintext_open": policy != "HTTPS_ONLY", "policy": policy}


def build_profile(cluster_info: dict, configs: Dict[str, dict], services: List[str],
                  components: List[dict], host_components: List[dict]) -> dict:
    security_type = cluster_info.get("security_type", "NONE")
    core = _props(configs, "core-site")
    kerberos = security_type == "KERBEROS" or core.get("hadoop.security.authentication", "simple") == "kerberos"

    profile = {
        "cluster": {
            "security_type": security_type,
            "kerberos": kerberos,
            "hadoop_auth": core.get("hadoop.security.authentication", "simple"),
            "rpc_protection": core.get("hadoop.rpc.protection", "authentication"),
            "realm": _props(configs, "kerberos-env").get("realm", ""),
        },
        "services": {},
    }
    svc = profile["services"]
    for name in services:
        svc[name] = {"auth": "kerberos" if kerberos else "simple", "endpoints": {},
                     "ranger_plugin": False, "ha": {}, "detected": []}

    def put(service, endpoint, data, rule):
        if service in svc:
            svc[service]["endpoints"][endpoint] = data
            svc[service]["detected"].append(rule)

    hdfs = _props(configs, "hdfs-site")
    if hdfs:
        put("HDFS", "web", _http_policy_endpoint(hdfs.get("dfs.http.policy")), "hdfs-site/dfs.http.policy")
        put("HDFS", "data_transfer", {"encrypted": _truthy(hdfs.get("dfs.encrypt.data.transfer", "false"))},
            "hdfs-site/dfs.encrypt.data.transfer")
    yarn = _props(configs, "yarn-site")
    if yarn:
        put("YARN", "web", _http_policy_endpoint(yarn.get("yarn.http.policy")), "yarn-site/yarn.http.policy")
    mapred = _props(configs, "mapred-site")
    if mapred:
        put("MAPREDUCE2", "web", _http_policy_endpoint(mapred.get("mapreduce.jobhistory.http.policy")),
            "mapred-site/mapreduce.jobhistory.http.policy")

    hive = _props(configs, "hive-site")
    if hive and "HIVE" in svc:
        svc["HIVE"]["auth"] = (hive.get("hive.server2.authentication") or "NONE").lower()
        put("HIVE", "hiveserver2", {"tls": _truthy(hive.get("hive.server2.use.SSL", "false")),
                                    "transport": hive.get("hive.server2.transport.mode", "binary")},
            "hive-site/hive.server2.use.SSL")

    hbase = _props(configs, "hbase-site")
    if hbase and "HBASE" in svc:
        svc["HBASE"]["auth"] = hbase.get("hbase.security.authentication", "simple").lower()
        put("HBASE", "web", {"tls": _truthy(hbase.get("hbase.ssl.enabled", "false"))}, "hbase-site/hbase.ssl.enabled")
        put("HBASE", "rpc", {"protection": hbase.get("hbase.rpc.protection", "authentication")},
            "hbase-site/hbase.rpc.protection")

    ranger_site = _props(configs, "ranger-admin-site")
    if ranger_site and "RANGER" in svc:
        admin_url = _props(configs, "admin-properties").get("policymgr_external_url", "")
        svc["RANGER"]["auth"] = ranger_site.get("ranger.authentication.method", "UNIX").lower()
        put("RANGER", "admin", {"tls": _truthy(ranger_site.get("ranger.service.https.attrib.ssl.enabled", "false")),
                                "url": admin_url.rstrip("/")},
            "ranger-admin-site/ranger.service.https.attrib.ssl.enabled")

    atlas = _props(configs, "application-properties")
    if atlas and "ATLAS" in svc:
        put("ATLAS", "server", {"tls": _truthy(atlas.get("atlas.enableTLS", "false"))},
            "application-properties/atlas.enableTLS")

    # Kafka family (KAFKA, KAFKA3, KAFKA4…): any service-owned "*-broker" type with `listeners`.
    for ctype, meta in configs.items():
        props = meta.get("properties") or {}
        if ctype.endswith("broker") and "listeners" in props and meta.get("service") in svc:
            listeners = parse_kafka_listeners(props["listeners"], props.get("listener.security.protocol.map", ""))
            service = meta["service"]
            for listener in listeners:
                put(service, f"listener_{listener['name'].lower()}", listener, f"{ctype}/listeners")
            client = [l for l in listeners if l["name"] != "CONTROLLER"]
            if client:
                svc[service]["auth"] = "kerberos" if all(l["sasl"] for l in client) else (
                    "mixed" if any(l["sasl"] for l in client) else "none")

    # Ranger plugin enabled: ranger-<x>-plugin-properties / *plugin-enabled = Yes
    for ctype, meta in configs.items():
        if re.match(r"^ranger-.+-plugin-properties$", ctype) and meta.get("service") in svc:
            props = meta.get("properties") or {}
            if any(k.endswith("plugin-enabled") and _truthy(v) for k, v in props.items()):
                svc[meta["service"]]["ranger_plugin"] = True
                svc[meta["service"]]["detected"].append(f"{ctype}/*plugin-enabled")

    # Service-specific JDKs (NiFi 2 -> 21, Trino -> 23, Cruise Control -> 11, …): *java_home / *java17_home / JAVA_HOME
    for ctype, meta in configs.items():
        if meta.get("service") not in svc:
            continue
        for key, value in (meta.get("properties") or {}).items():
            if JAVA_HOME_KEY.search(key) and isinstance(value, str) and value.strip().startswith("/"):
                homes = svc[meta["service"]].setdefault("java_homes", [])
                if value.strip() not in homes:
                    homes.append(value.strip())

    # HA: any MASTER component installed on more than one host.
    category = {c["component_name"]: c.get("category") for c in components}
    hosts_per_comp: Dict[str, set] = {}
    service_of = {}
    for hc in host_components:
        hosts_per_comp.setdefault(hc["component_name"], set()).add(hc["host_name"])
        service_of[hc["component_name"]] = hc.get("service_name")
    for comp, hosts in hosts_per_comp.items():
        if category.get(comp) == "MASTER" and service_of.get(comp) in svc:
            svc[service_of[comp]]["ha"][comp] = len(hosts) > 1

    ha_components = sorted(c for s in svc.values() for c, is_ha in s["ha"].items() if is_ha)
    profile["cluster"]["ha_components"] = ha_components
    # "HA cluster" = HDFS NameNode HA (what deploy jobs call *_HA); other services may still have HA masters.
    profile["cluster"]["ha"] = svc.get("HDFS", {}).get("ha", {}).get("NAMENODE", False)
    return profile
