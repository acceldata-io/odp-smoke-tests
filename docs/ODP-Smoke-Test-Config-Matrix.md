# Supporting Every Security / Topology Combination

Companion to `ODP-Smoke-Test-Pipeline-Plan.md` and `ODP-Smoke-Test-Cases.md`.

## 1. The problem

Clusters come in many shapes — Kerberos on/off, TLS on/off, and these are **not cluster-wide switches**:
the reference cluster (rl10test) is already *mixed*:

| Setting | Value on rl10test |
|---|---|
| Hadoop auth (`hadoop.security.authentication`) | `kerberos` |
| Hadoop RPC protection | `authentication,privacy` (encrypted RPC accepted) |
| HDFS / YARN web UI | `HTTP_ONLY` (no TLS) |
| HiveServer2 | Kerberos, **no TLS** |
| HBase | Kerberos, RPC `authentication` |
| Kafka / Kafka3 | `SASL_PLAINTEXT` (Kerberos, no TLS) |
| Kafka4 | `PLAINTEXT` (**no Kerberos, no TLS**) |
| Ranger Admin | HTTP, UNIX auth |
| Atlas | TLS off |
| **Trino** | **HTTPS on + Kerberos** |
| Ambari Server | HTTP |

So the design rule is: **never ask "is the cluster secure?" — ask "how is *this endpoint* secured?"**

## 2. Design: detect → adapt → assert

```
            Ambari configs (REST)                 ┌──────────────────────────────┐
  discover ───────────────────────► ClusterProfile│ per service / per endpoint:  │
                                                  │  auth: kerberos|simple|ldap  │
                                                  │  tls:  on|off  (+truststore) │
                                                  │  ha:   on|off                │
                                                  │  ranger_plugin: on|off       │
                                                  │  wire: rpc privacy, DT encr. │
                                                  └──────────────┬───────────────┘
                                                                 │
          ┌──────────────────────────────────────────────────────┼─────────────────────────┐
          ▼                                                      ▼                         ▼
   Identity layer                                     Connection layer               Expectation layer
   run_as(user)                                       build URL / CLI flags          what "correct" means
   • kerberos → kinit -kt <keytab>                    • http vs https + port         for THIS profile
     in private KRB5CCNAME                            • --negotiate vs user.name=    (see §4)
   • simple   → sudo -u <user> /                      • beeline ;ssl=true;
     HADOOP_USER_NAME / ?user.name=                     principal=… / ;user=…
   • ldap     → user + password                       • kafka security.protocol
                                                        + sasl/ssl client props
                                                      • truststore from Ambari
```

**Test functions never contain `if kerberos` / `if ssl`.** They call e.g.
`hdfs.run(["-put", f, p], as_user="newuser_1")` or `ranger.client()`; the framework picks the right
command, URL, port, credentials and truststore from the profile. One test file therefore runs
unchanged on every combination.

### 2.1 `ClusterProfile` — detection rules (read from Ambari config API)

Candidate keys below; each is verified against the installed stack version when the detector is coded,
and the detector has unit tests (§6).

| Service / endpoint | Auth detection | TLS detection |
|---|---|---|
| Cluster | `Clusters/security_type` (`KERBEROS`/`NONE`), `core-site/hadoop.security.authentication` | — |
| Hadoop RPC | — | `core-site/hadoop.rpc.protection` (privacy = encrypted) |
| HDFS UI/WebHDFS | `core-site/hadoop.http.authentication.type` | `hdfs-site/dfs.http.policy` (HTTP_ONLY / HTTPS_ONLY / HTTP_AND_HTTPS) |
| HDFS data transfer | — | `hdfs-site/dfs.encrypt.data.transfer`, `dfs.data.transfer.protection` |
| YARN / MR JHS | as Hadoop | `yarn-site/yarn.http.policy`, `mapred-site/mapreduce.jobhistory.http.policy` |
| HiveServer2 | `hive.server2.authentication` (KERBEROS/LDAP/NONE/…) | `hive.server2.use.SSL`; transport `binary`/`http` |
| HBase | `hbase.security.authentication` | `hbase.ssl.enabled` (UI), `hbase.rpc.protection` |
| ZooKeeper | SASL jaas present | `secureClientPort` / `client.secure` |
| Kafka (each variant, each listener) | listener protocol `SASL_*` | listener protocol `*SSL` (PLAINTEXT / SSL / SASL_PLAINTEXT / SASL_SSL) |
| Ranger Admin | `ranger.authentication.method` (UNIX/LDAP/AD), SPNEGO | `ranger.service.https.attrib.ssl.enabled` |
| Ranger plugin → Admin | — | `policymgr_external_url` scheme + `ranger-<svc>-policymgr-ssl` |
| Ranger KMS | `hadoop.kms.authentication.type` | KMS https flag / `kms://https@` in key provider path |
| Solr / Infra Solr | Kerberos plugin in `security.json` | `*_ssl_enabled` in env config |
| Atlas | `atlas.authentication.method.kerberos` / file / ldap | `atlas.enableTLS` |
| Trino | `http-server.authentication.type` | `http-server.https.enabled` |
| Livy | `livy.server.auth.type` | `livy.keystore` set |
| Spark History / Thrift | SPNEGO filter / thrift auth | `spark.ssl.*` |
| Oozie, Druid, Pinot, Impala, Kudu, Ozone, NiFi, Airflow, Superset, Hue, … | per-service rule (same pattern) | per-service rule |
| Knox, NiFi 2 | always authenticated | always TLS |
| Ambari Server | — | `api.ssl` in `ambari.properties` |

Output: `reports/profile.json` (archived by Jenkins) — first thing to read when a test fails.

### 2.2 Identity layer (who runs the test)
| Cluster auth | How `run_as("newuser_1")` works |
|---|---|
| Kerberos | `kinit -kt /etc/security/keytabs/newuser_1.keytab` into a **private ticket cache per user per test** (`KRB5CCNAME=/tmp/smoke_<run_id>_<user>`) — parallel tests never clash |
| Simple (no Kerberos) | `sudo -u newuser_1 …`, `HADOOP_USER_NAME`, `?user.name=` for REST, `--user` for Kafka ACL principal `User:ANONYMOUS`-aware rules |
| LDAP (Hive/Ranger/Knox/Superset…) | username + password from Jenkins credentials |

Test users are **created by the suite's bootstrap if missing** (OS user + group on all hosts, principal + keytab via `kadmin` when Kerberos is on) so Ranger allow/deny tests work on any cluster.

### 2.3 TLS handling
- Truststore discovered from Ambari (`ssl-client/ssl.client.truststore.location`, service-specific truststores); PEM bundle extracted once for `curl`/`requests`.
- Functional tests **verify certificates** (never `-k`/`verify=False`) — so a broken cert shows up as a failure instead of being hidden.
- Mixed `HTTP_AND_HTTPS` → tests run against both ports.

## 3. New / revised standard suites

### 3.1 STD-AUTH (replaces STD-KRB; mode-aware)
| ID | Kerberos ON | Kerberos OFF (simple) | LDAP |
|---|---|---|---|
| AUTH-01 no credentials | must be **rejected** | must **succeed** as anonymous/`dr.who` or OS user (record INFO "unauthenticated access") | rejected |
| AUTH-02 valid credentials | succeeds | succeeds as `sudo -u` user | succeeds |
| AUTH-03 wrong identity | wrong keytab/principal → rejected | N/A | wrong password → rejected |
| AUTH-04 web UI | 401 without SPNEGO, 200 with | 200 | login form / 401 |
| AUTH-05 service keytabs | valid, perms 400/440, not expiring | N/A | N/A |
| AUTH-06 impersonation (proxyuser) | Livy/HS2/Oozie `doAs` app owner == end user | same, via `user.name` | same |
| AUTH-07 auth_to_local mapping | `newuser_1@REALM` → `newuser_1` (`hadoop kerbname`) | N/A | N/A |

### 3.2 STD-TLS (new; runs per endpoint where TLS is ON)
| ID | Tier | Test | Pass criteria |
|---|---|---|---|
| TLS-01 | B | Handshake with cluster truststore | succeeds, chain verifies |
| TLS-02 | B | Certificate hostname (SAN/CN) | matches host FQDN |
| TLS-03 | B | Expiry | > 30 days (WARN), > 0 (FAIL) |
| TLS-04 | B | Protocols | TLS 1.2+ only; TLS 1.0/1.1/SSLv3 rejected |
| TLS-05 | M | Weak ciphers (RC4, 3DES, NULL, EXPORT) | rejected |
| TLS-06 | B | Plain HTTP port when policy is HTTPS_ONLY | closed or redirects — not serving data |
| TLS-07 | M | Keystore/truststore files | exist, readable by service user only, password from config works |
| TLS-08 | M | Kafka `SSL`/`SASL_SSL` listener: produce/consume over TLS | OK |
| TLS-09 | M | Ranger plugin → Ranger Admin over TLS (policy download works) | covered by STD-RGR-04 using HTTPS URL |
| TLS-10 | M | Two-way TLS (Ambari agent ↔ server, Kafka client auth) when configured | client w/o cert rejected |

When TLS is **OFF** for an endpoint: STD-TLS is reported **N/A (TLS disabled)** plus one INFO line
"endpoint X serves plaintext" — so the report shows the security posture at a glance.

### 3.3 STD-WIRE (encryption on the wire beyond HTTP)
| ID | Test | Pass criteria |
|---|---|---|
| WIRE-01 | `hadoop.rpc.protection` consistency across all hosts' core-site | identical everywhere (mismatch = "SASL negotiation" failures) |
| WIRE-02 | HDFS data transfer encryption on → put/get works | OK |
| WIRE-03 | HBase / ZK SASL QoP consistent with Hadoop | consistent |

### 3.4 How existing suites adapt
| Suite | Kerberos ON | Kerberos OFF |
|---|---|---|
| STD-RGR (Ranger) | identity = Kerberos principal → short name | identity = OS user / `user.name`; still runs (Ranger works without Kerberos), deny→allow asserted the same way |
| STD-AUD | audit `reqUser` = `newuser_1` | same; `cliIP` checked |
| STD-RGR-02 Test Connection | repo uses lookup principal/keytab | repo uses username/password |
| STD-LOG | + check no `GSSException`, `Clock skew`, `Checksum failed` lines | + check no unexpected `AccessControlException` |
| Ambari service check | unchanged (Ambari adapts itself) | unchanged |

## 4. Expectation matrix (what "pass" means per combination)

| Combination | Example expectations that change |
|---|---|
| **No Kerberos, no TLS** | AUTH-01 succeeds anonymously (INFO); TLS N/A; Ranger still enforced; WebHDFS via `?user.name=` |
| **No Kerberos, TLS** | AUTH-01 succeeds; STD-TLS fully runs; all URLs https with truststore |
| **Kerberos, no TLS** (rl10test, mostly) | AUTH-01 must be rejected; SPNEGO required; TLS N/A |
| **Kerberos + TLS** | both suites fully run; Kafka `SASL_SSL`; beeline `ssl=true;principal=…` |
| **Mixed** (rl10test: Trino HTTPS, rest HTTP; Kafka4 PLAINTEXT) | evaluated per endpoint; report shows a "posture" table |
| **LDAP for Hive/Ranger/Knox** | AUTH with password; Ranger usersync from LDAP groups |
| **HA off** | HA tests (1 active + standby) → N/A; failover-readiness skipped |
| **Ranger plugin off for a service** | STD-RGR/AUD → SKIP "plugin not enabled" |

Optional **policy file** (`configs/pipeline/expected_posture.yaml`) lets a team declare what a cluster *should* be, e.g.
`kafka4: {auth: kerberos}` → then Kafka4 PLAINTEXT becomes a **FAIL** instead of INFO. Without the file, the
suite tests the cluster *as configured* and just reports the posture.

## 5. Other axes the same mechanism covers
| Axis | Handled by |
|---|---|
| HA vs non-HA (NN, RM, HMS/HS2, HBase master, Atlas, Ranger, KMS, OM/SCM) | profile `ha` flag; HA tests auto-N/A |
| Service subset / versions installed (Spark 2/3/4, Kafka 2/3/4 …) | discovery; templates parametrized per variant |
| Ranger plugin enabled per service | profile `ranger_plugin` |
| Audit destinations (Solr / HDFS / both / none) | `xasecure.audit.destination.*` |
| Single-node vs multi-node | replication/RF computed from host counts |
| OS (RHEL 8/9, Rocky 10, Ubuntu 22) | only bootstrap differs (package manager); tests use cluster CLIs |
| Ambari DB / Ranger DB / Hive Metastore DB (MySQL/Postgres/Oracle) | DB connectivity test reads JDBC URL from config |

## 5a. Runtime axes: Python, JDK, HA

Clusters differ in Python (2.7 may be present next to 3.11 / 3.12), JDK (8 / 11 / 17 / 21, often several installed
side by side, plus service-specific JDKs) and HA vs non-HA. None of these are pipeline parameters — all are **detected**.

| Axis | How it is handled |
|---|---|
| **Python** (test runner) | `bin/remote_run.sh` uses the cluster's newest system `python3.13…3.8` (3.11 / 3.12 on ODP clusters); Python 2 and 3.6 are ignored. If none exists the run stops with a clear error. Test libraries go into a venv keyed by interpreter + requirements, so the system Python is never modified. |
| **Python** (cluster) | Recorded per host in `run_summary.json` → `topology.runtime` (system interpreters and the Ambari agent's Python via `ambari-python-wrap`). |
| **JDK** | Stack JDK = `stack.java.home` (else `java.home`) from `ambari.properties`. Service-specific JDKs = any Ambari config key matching `*java_home` / `*java17_home` / `JAVA_HOME` with an absolute path (rl10test: `nifi2-env` → 21, `nifi-registry2-env` → 21, `trino-env` / `trino-gateway-env` → `/opt/jdk-23`, `cruise-control-env` → 11). |
| **HA / non-HA** | `profile.cluster.ha` = HDFS NameNode HA; `profile.cluster.ha_components` = every MASTER on > 1 host; per-service `ha` map. |

### STD-RT — runtime suite (implemented: `tests/test_02_runtime.py`)
| ID | Tier | Test | Pass criteria |
|---|---|---|---|
| RT-01 | B | Stack JDK has a runnable `bin/java` on every host | `java -version` parses (8/11/17/21/…) |
| RT-02 | B | Same stack JDK major on all hosts | one major version |
| RT-03 | B | Every running JVM (except Ambari server) runs on the stack JDK **or** a JDK configured for a service in Ambari (`readlink /proc/<pid>/exe`) | no JVM on an unconfigured JDK (catches services silently picking up another installed JDK) |
| RT-04 | M | Client tools (`hdfs`, `beeline`, `spark-submit`, `kafka-*`) resolve the expected JDK | planned |

### What changes per JDK (expectations used by other suites)
| JDK | Impact on tests |
|---|---|
| 8 | GC logs use `-XX:+PrintGCDetails` style files; TLS 1.0/1.1 disabled only on recent 8u builds; legacy Kerberos enctypes may still be accepted |
| 11 | Unified logging GC (`-Xlog:gc`); TLS 1.3 available |
| 17 / 21 | Strong encapsulation: services need `--add-opens` (missing flags show up as `InaccessibleObjectException` → STD-LOG allowlist must not hide these); weak Kerberos enctypes (DES, RC4) disabled by default → keytabs must contain AES keys (STD-AUTH-05 checks `klist -e`); TLS 1.0/1.1 disabled |
| Service-specific (NiFi 2 → 21, Trino → 23+) | RT-03 allows them only if configured in Ambari |

### What changes per HA mode
| Check | HA cluster | Non-HA cluster |
|---|---|---|
| HDFS | `haadmin -getAllServiceState`: exactly 1 active; ZKFC + JournalNode quorum healthy | SecondaryNameNode running and checkpointing (`fs.checkpoint` age < threshold); HA tests → N/A |
| YARN | `rmadmin -getAllServiceState`: 1 active RM | single RM up; HA tests → N/A |
| HBase / Atlas / Ranger / KMS / Hive (HS2, HMS) / Oozie / Ozone (OM, SCM) | one active + standbys (or all instances serve requests: HS2 via ZK discovery, KMS/Ranger each instance) | single instance up |
| Clients | use the HA nameservice / ZK discovery URL | direct host URL |

## 6. Making sure the suite itself works on combinations we don't have

1. **Profile fixtures**: snapshot Ambari configs (`/api/v1/clusters/<c>/configurations/service_config_versions?is_current=true`)
   from every cluster we touch (secrets stripped) into `tests_framework/fixtures/<cluster>.json`.
2. **Unit tests** for the detector + connection builders against those fixtures, plus synthetic ones
   (e.g. rl10test fixture with `dfs.http.policy=HTTPS_ONLY` flipped) — assert the built URL/flags/JDBC string.
   Runs in seconds, no cluster needed, on every commit.
3. **Live runs** against real clusters of each combination (§7).

## 7. Jenkins: running the matrix

**Inventory file** (`configs/pipeline/clusters.yaml`) — one entry per available cluster, tagged by combination:
```yaml
rl10test:   {ambari: http://10.101.11.82:8080, executor: 10.101.11.82, tags: [kerberos, tls-partial, ha]}
cluster-b:  {ambari: https://…:8443,           executor: …,            tags: [kerberos, tls, ha]}
cluster-c:  {ambari: http://…:8080,            executor: …,            tags: [simple, no-tls, non-ha]}
```

**Jenkinsfile** — `matrix` over the clusters (or a `CLUSTERS` parameter to pick some):
```groovy
matrix {
  axes { axis { name 'CLUSTER'; values 'rl10test', 'cluster-b', 'cluster-c' } }
  stages {
    stage('Discover') { … }   // writes profile.json → shown in build description
    stage('Test')     { … }   // same suite, adapts per profile
  }
}
post { always { junit '**/reports/*.xml' } }  // one combined report, grouped by cluster
```
The tags only *label* results; the suite trusts what it **detects**, not the tag (a tag mismatch is itself reported).

**Getting clusters for missing combinations** (phased):
| Option | Effort | Notes |
|---|---|---|
| A. Use existing team clusters as they are; tag them | none | start here |
| B. Re-use one cluster and toggle (Ambari "Enable Kerberos"/"Disable Kerberos" API, TLS config + restart) | medium | slow (full restarts), risky on shared clusters → only on a dedicated QA cluster |
| C. Provision fresh clusters per combination (Ambari Blueprint + Ansible, then tear down) | high | best long-term; nightly matrix of 4 combos. Reuse the existing `~/odp-build/Jenkins` deploy job (already has a *kerberos selection* stage, agent `usdc_runner`) + `~/odp-build/ansible-odp`, and trigger this suite as a downstream job |

Combination count grows fast (auth × TLS × HA × LDAP × OS …). Use **pairwise selection**: choose ~4–6 clusters so that
every *pair* of settings appears at least once, instead of all permutations.
Suggested minimum set:
1. Kerberos + TLS + HA + JDK 17 + Python 3.12 (production-like)
2. Kerberos + no TLS + HA + JDK 17 + Python 3.12 (rl10test)
3. No Kerberos + TLS + non-HA + JDK 11 + Python 3.11
4. No Kerberos + no TLS + non-HA + JDK 8 + Python 3.11 with Python 2 also installed (oldest)
5. Kerberos + TLS + HA + JDK 21 + Python 3.11
6. LDAP (Ranger/Hive/Knox) + Kerberos + TLS (add later)

## 8. Changes to the plan
- Phase 1 now also includes: `ClusterProfile` detector + Identity/Connection layers + `profile.json` + STD-AUTH + STD-TLS + detector unit tests.
- Every case in `ODP-Smoke-Test-Cases.md` is written mode-agnostic; STD-KRB there is superseded by STD-AUTH here.
