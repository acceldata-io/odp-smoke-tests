"""Offline unit tests for the ClusterProfile detector (no cluster needed).

Run: python -m pytest tests_framework -p no:cacheprovider -o addopts=""
"""
from framework.profile import build_profile, parse_kafka_listeners


def cfg(service, **props):
    return {"service": service, "properties": props}


COMPONENTS = [
    {"component_name": "NAMENODE", "category": "MASTER"},
    {"component_name": "DATANODE", "category": "SLAVE"},
    {"component_name": "KAFKA_BROKER", "category": "MASTER"},
]
HOST_COMPONENTS = [
    {"component_name": "NAMENODE", "host_name": "h1", "service_name": "HDFS"},
    {"component_name": "NAMENODE", "host_name": "h2", "service_name": "HDFS"},
    {"component_name": "DATANODE", "host_name": "h1", "service_name": "HDFS"},
    {"component_name": "KAFKA_BROKER", "host_name": "h1", "service_name": "KAFKA4"},
]


def test_kafka_listeners_with_protocol_map():
    out = parse_kafka_listeners("PLAINTEXT://:9092,CONTROLLER://:9093", "CONTROLLER:PLAINTEXT,PLAINTEXT:PLAINTEXT")
    assert [(l["name"], l["protocol"], l["port"], l["tls"], l["sasl"]) for l in out] == [
        ("PLAINTEXT", "PLAINTEXT", "9092", False, False), ("CONTROLLER", "PLAINTEXT", "9093", False, False)]
    sasl_ssl = parse_kafka_listeners("SASL_SSL://h:6668")[0]
    assert sasl_ssl["tls"] and sasl_ssl["sasl"]


def test_kerberized_mixed_cluster_like_rl10test():
    configs = {
        "core-site": cfg("HDFS", **{"hadoop.security.authentication": "kerberos",
                                    "hadoop.rpc.protection": "authentication,privacy"}),
        "hdfs-site": cfg("HDFS", **{"dfs.http.policy": "HTTP_ONLY"}),
        "kafka4-broker": cfg("KAFKA4", listeners="PLAINTEXT://:9092,CONTROLLER://:9093"),
        "kafka-broker": cfg("KAFKA", listeners="SASL_PLAINTEXT://h1:6667"),
        "ranger-hdfs-plugin-properties": cfg("HDFS", **{"ranger-hdfs-plugin-enabled": "Yes"}),
        "ranger-kafka-plugin-properties": cfg("KAFKA", **{"ranger-kafka-plugin-enabled": "No"}),
    }
    p = build_profile({"security_type": "KERBEROS"}, configs, ["HDFS", "KAFKA", "KAFKA4"], COMPONENTS, HOST_COMPONENTS)
    assert p["cluster"]["kerberos"] is True
    assert p["services"]["HDFS"]["endpoints"]["web"] == {"tls": False, "plaintext_open": True, "policy": "HTTP_ONLY"}
    assert p["services"]["HDFS"]["ranger_plugin"] is True
    assert p["services"]["HDFS"]["ha"] == {"NAMENODE": True}
    assert p["services"]["KAFKA"]["auth"] == "kerberos" and p["services"]["KAFKA"]["ranger_plugin"] is False
    assert p["services"]["KAFKA4"]["auth"] == "none"          # PLAINTEXT on a Kerberized cluster
    assert p["services"]["KAFKA4"]["ha"] == {"KAFKA_BROKER": False}


def test_unsecured_tls_cluster():
    configs = {
        "hdfs-site": cfg("HDFS", **{"dfs.http.policy": "HTTPS_ONLY"}),
        "hive-site": cfg("HIVE", **{"hive.server2.authentication": "NONE", "hive.server2.use.SSL": "true"}),
    }
    p = build_profile({"security_type": "NONE"}, configs, ["HDFS", "HIVE"], COMPONENTS, HOST_COMPONENTS)
    assert p["cluster"]["kerberos"] is False and p["services"]["HDFS"]["auth"] == "simple"
    assert p["services"]["HDFS"]["endpoints"]["web"]["tls"] is True
    assert p["services"]["HDFS"]["endpoints"]["web"]["plaintext_open"] is False
    assert p["services"]["HIVE"]["auth"] == "none" and p["services"]["HIVE"]["endpoints"]["hiveserver2"]["tls"] is True


def test_profile_never_contains_raw_secrets():
    configs = {"ranger-admin-site": cfg("RANGER", **{"ranger.service.https.attrib.ssl.enabled": "false",
                                                     "ranger.jpa.jdbc.password": "SECRET_VALUE"}),
               "admin-properties": cfg("RANGER", policymgr_external_url="http://h1:6080/")}
    p = build_profile({"security_type": "NONE"}, configs, ["RANGER"], [], [])
    assert "SECRET_VALUE" not in repr(p)
    assert p["services"]["RANGER"]["endpoints"]["admin"]["url"] == "http://h1:6080"


def test_ha_summary_non_ha_cluster():
    comps = [{"component_name": "NAMENODE", "category": "MASTER"}, {"component_name": "SECONDARY_NAMENODE", "category": "MASTER"}]
    hcs = [{"component_name": "NAMENODE", "host_name": "h1", "service_name": "HDFS"},
           {"component_name": "SECONDARY_NAMENODE", "host_name": "h2", "service_name": "HDFS"}]
    p = build_profile({"security_type": "NONE"}, {}, ["HDFS"], comps, hcs)
    assert p["cluster"]["ha"] is False and p["cluster"]["ha_components"] == []


def test_ha_summary_ha_cluster():
    p = build_profile({"security_type": "KERBEROS"}, {}, ["HDFS", "KAFKA4"], COMPONENTS, HOST_COMPONENTS)
    assert p["cluster"]["ha"] is True and p["cluster"]["ha_components"] == ["NAMENODE"]


def test_service_java_homes_collected():
    configs = {"nifi2-env": cfg("NIFI2", nifi_java_home="/usr/lib/jvm/java-21-openjdk"),
               "kraft4-broker-env": cfg("KAFKA4", java17_home="/usr/lib/jvm/java-17-openjdk"),
               "trino-env": cfg("TRINO", java_home="/opt/jdk-23", java_opts="-Xmx1g"),
               "pinot-env": cfg("PINOT", JAVA_HOME="${JAVA_HOME}")}           # not absolute -> ignored
    p = build_profile({"security_type": "NONE"}, configs, ["NIFI2", "KAFKA4", "TRINO", "PINOT"], [], [])
    assert p["services"]["NIFI2"]["java_homes"] == ["/usr/lib/jvm/java-21-openjdk"]
    assert p["services"]["KAFKA4"]["java_homes"] == ["/usr/lib/jvm/java-17-openjdk"]
    assert p["services"]["TRINO"]["java_homes"] == ["/opt/jdk-23"]
    assert "java_homes" not in p["services"]["PINOT"]
