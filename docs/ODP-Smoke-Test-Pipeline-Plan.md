# ODP Cluster Smoke-Test Pipeline — Plan

Reference cluster: rl10test1 (10.101.11.82, Ambari Server), rl10test2 (10.101.11.89), rl10test3 (10.101.11.114)
ODP 3.3.6.6, Rocky Linux 10.2, Python 3.12, Kerberized (realm `ADSRE.COM`), NameNode HA.

---

## 1. What we found on the cluster (drives the design)

| Fact | Why it matters |
|---|---|
| ~60 Ambari services, many with **multiple versions side by side** (Spark 2 / 3 / 3.3.3 / 3.5.1 / 4, Kafka / Kafka3 / Kafka4, 5 Livy variants, Cruise Control 3/4) | Test list must be **discovered from Ambari**, not hard-coded, or it will rot. |
| Kerberos enabled, keytabs in `/etc/security/keytabs` | Every test needs a `kinit` step (get a Kerberos ticket) before calling a service. |
| Ranger plugins found enabled for: HDFS, YARN, HBase, Kafka (x3), Ozone, KMS, Atlas, Schema Registry, Trino. **Not** found for Hive, Impala, Knox, Solr, NiFi, Kudu | "Is plugin X enabled?" must be discovered per run, not assumed. |
| Ranger audit destination = Solr (`ranger.audit.source.type=solr`) | Audit checks query the Solr `ranger_audits` collection (+ HDFS audit dir if enabled). |
| Test users `newuser_1`, `newuser_2` exist | Useful for Ranger allow/deny tests (a user with and without a policy). |
| Ansible not installed on cluster nodes; root crontab has Ansible-managed entries | Ansible is already used for provisioning elsewhere — fine for bootstrap, not needed for tests. |
| `/var/log/odp-log-validation/` holds **0-byte** reports from an earlier tool (not on this cluster) | A previous log-validation attempt exists somewhere; worth locating and absorbing. Also: our log checker must flag empty files. |

---

## 2. Tech stack — recommendation

| Layer | Choice | Reason |
|---|---|---|
| Test engine | **Python 3 + pytest** | Rich assertions, fixtures (shared setup/teardown), parametrization (one test function → N services/hosts), retries, timeouts, and standard reports. Ranger/Ambari/Solr are all REST APIs — Python `requests` handles these naturally. |
| Orchestrator | **Jenkins** (declarative `Jenkinsfile`) | Scheduling (nightly / post-upgrade), parameters, stage view, history of results, artifacts, notifications. |
| Remote execution | `paramiko` / plain `ssh` from Python | For per-host checks (log files, policy cache files). |
| Bootstrap (optional) | Ansible | Only to push the suite + install a venv on the executor node, since the team already uses Ansible. |
| Reports | JUnit XML + `pytest-html` + JSON summary | JUnit XML is the file format Jenkins reads to show pass/fail per test and trend graphs; HTML is a human-friendly report. |

**Why not Ansible as the main tool:** Ansible is designed to *make machines match a desired state*. It is weak at assertions, complex polling (e.g., "wait until Ranger plugin downloads policy v42"), result reporting, and per-test pass/fail. You would end up writing Python modules inside Ansible anyway.

**Why not only Ambari service checks:** they are shallow (e.g., HDFS check just writes one file). They don't verify Ranger enforcement, audits, policy download, or logs.

---

## 3. Architecture

```
Jenkins ──ssh──> executor node (rl10test1)          ┌──> Ambari REST  (discovery, service checks, alerts)
                  python venv + pytest  ────────────┼──> Ranger REST  (policies, test-connection, plugin status)
                  kinit with keytabs                ├──> Solr         (ranger_audits)
                  writes JUnit/HTML ──> Jenkins     ├──> service CLIs (hdfs, beeline, kafka-*, hbase, trino…)
                                                    └──ssh──> every host (log files, policy cache)
```

Tests run **on a cluster node**, not on the Jenkins machine, because that node already has: client binaries, `krb5.conf`, keytabs, and network access to all services.

### Repository layout (proposed)

```
odp-smoke-tests/
├── requirements.txt
├── configs/pipeline/
│   ├── cluster.yaml            # ambari url, cluster name, executor host (no secrets)
│   └── log_error_allowlist.yaml# known-noisy ERROR lines to ignore
├── framework/
│   ├── ambari.py               # REST client: services, components, hosts, configs, run service check, alerts
│   ├── kerberos.py             # kinit / kdestroy helpers, principal lookup from Ambari configs
│   ├── ranger.py               # policies CRUD, test-connection, plugin status, audits
│   ├── solr.py
│   ├── remote.py               # ssh exec / file stat on cluster hosts
│   ├── logs.py                 # log-dir discovery + validation rules
│   └── registry.py             # maps Ambari service name -> test plugin
├── plugins/                    # one module per service family
│   ├── hdfs.py  yarn.py  hive.py  kafka.py  hbase.py  zookeeper.py  ozone.py
│   ├── spark.py (handles all spark variants)  livy.py  trino.py  impala.py  kudu.py
│   └── ranger.py  atlas.py  solr.py  knox.py  oozie.py  nifi.py  druid.py  pinot.py …
├── tests/
│   ├── conftest.py             # fixtures: discovery, run_id, kinit, cleanup
│   ├── test_00_health.py
│   ├── test_10_service_checks.py
│   ├── test_20_functional.py   # parametrized over discovered services × tier
│   ├── test_30_ranger.py
│   └── test_40_logs.py
└── reports/                    # generated
```

A **plugin registry** maps each Ambari service name (e.g. `SPARK3`, `KAFKA4`) to its test module. A discovered service with **no plugin is reported as "NOT COVERED"** (visible in the report), never silently skipped.

---

## 4. Test layers (run in this order)

### Layer 0 — Health (fast, ~1 min)
- Ambari: every host heartbeating; every non-client component `STARTED`; no maintenance-mode surprises.
- Ambari alerts: list `CRITICAL` / `WARNING` alerts.
- Kerberos: `kinit` works for smoke user + each headless keytab.

### Layer 1 — Ambari service checks (all services)
- `POST /api/v1/clusters/<c>/requests` with command `<SERVICE>_SERVICE_CHECK`, then poll the request until `COMPLETED`/`FAILED`; on failure, pull the task's stdout/stderr into the report.
- ~60 services × 1–3 min ≈ 1–2 h; Ambari queues requests, so this stage is the long pole. Parameter to skip/limit it.

### Layer 2 — Functional tests (basic / medium tiers)
Each test uses a unique prefix `smoke_<run_id>` and cleans up after itself.

| Service | Basic | Medium |
|---|---|---|
| HDFS | put/get/ls/rm, `dfsadmin -report`, safemode off, HA state (1 active NN) | NN failover-readiness check, trash, snapshot create/delete, encryption zone write/read (with KMS), WebHDFS & HttpFS read |
| YARN / MR2 | RM active, NM count, queue list | run MR pi / wordcount, distributed shell, check JobHistory & Timeline have the app |
| ZooKeeper | `ruok`/`srvr` on each server | create/read/delete znode with SASL |
| Hive | beeline connect via ZK discovery | create DB/table, insert, select, ACID table, Tez query, drop |
| HBase | `status` | create table, put/get/scan, disable/drop; Phoenix create/upsert/select |
| Kafka / Kafka3 / Kafka4 | broker list, topic list | create topic, produce/consume N msgs with SASL, consumer-group describe, delete topic |
| Spark (all variants) | `spark-submit` SparkPi on YARN | read/write HDFS, Hive table via Spark SQL, History Server shows app; Thrift server via beeline; Spark Connect |
| Livy (all variants) | `/sessions` endpoint | submit batch + interactive statement |
| Ozone | `ozone admin` health, SCM/OM status | volume/bucket/key put/get, S3 gateway via `aws s3` |
| Trino / Impala | `SELECT 1` | query a Hive table, Kudu table (Impala) |
| Kudu | cluster ksck | create/insert/scan via Impala or CLI |
| Solr / Infra-Solr | collections list | create/query/delete test collection |
| Atlas | `/api/atlas/admin/status` ACTIVE | search entity created by the Hive test (lineage hook works) |
| Knox | gateway up | WebHDFS via Knox topology |
| Oozie | `oozie admin -status` | run a shell workflow |
| NiFi2 / Registry | API reachable | create & start/stop a tiny flow |
| Druid / Pinot | coordinator/controller health | ingest small dataset + query |
| Others (Airflow3, Superset, Hue, Zeppelin, JupyterHub, MLflow, SeaTunnel, Schema Registry, Cruise Control, Celeborn, Flink, Sqoop, Trino Gateway) | UI/API health endpoint | one representative operation each (Phase 3) |

### Layer 3 — Ranger (cross-cutting)
1. **Admin health**: login, service-defs list, usersync users present (e.g., `newuser_1`).
2. **Repos & test connection**: for every Ranger service repo, run "Test Connection" (the same call the Ranger UI makes) — pass if the lookup succeeds.
3. **Policy types** (per supported service-def): create → verify → delete
   - Resource-based access policy (all service types)
   - Data masking & row-level filter (Hive / Trino where supported)
   - Tag-based policy, security zone (medium tier)
4. **Policy download**: after creating a policy, wait until each enabled plugin picks it up — checked two ways:
   - Ranger's plugin-status info (policy version downloaded / activated per host)
   - plugin's local cache file `/etc/ranger/<repo>/policycache/*.json` mtime/version on the host
5. **Enforcement**: as `newuser_1` access a resource → expect DENY; add allow policy → wait for download → expect ALLOW.
6. **Audit**: the above access appears in Solr `ranger_audits` (query by resource name containing `run_id`) and in the HDFS audit dir `/ranger/audit/<service>/<yyyymmdd>` if HDFS audit is enabled.
7. **KMS**: create key, `hadoop key list`, roll, delete.

> Exact Ranger REST endpoints will be verified against the installed Ranger version before coding.

### Layer 4 — Log validation (all components, all hosts)
Log directory is **read from Ambari configs** (e.g. `hadoop-env/hdfs_log_dir_prefix`, `hive-env/hive_log_dir`, `kafka-env/kafka_log_dir`) — not hard-coded.

For each (component, host):
- log dir exists and is owned by the service user
- the main log file exists and is **not empty**
- file was **modified after the test run started** (proves the component is actually logging the activity we generated)
- **no new `ERROR` / `FATAL` / `Exception` lines since run start**, minus an allowlist of known-noisy lines (`configs/pipeline/log_error_allowlist.yaml`)
- rotated files present (log rotation working) — medium tier
- disk usage of log dir under a threshold

---

## 5. Jenkins pipeline

```
Parameters: AMBARI_HOST, EXECUTOR_HOST, SERVICES (default: all), TIER (basic|medium),
            RUN_SERVICE_CHECKS (bool), RUN_RANGER (bool), RUN_LOGS (bool)
Credentials (Jenkins credential store): ambari-admin, ranger-admin, cluster-ssh-key

Stages:
 1. Checkout
 2. Bootstrap      – rsync suite to executor, create/refresh venv
 3. Discover       – dump services/components/hosts/log dirs to discovery.json (archived)
 4. Health         – Layer 0
 5. Service Checks – Layer 1
 6. Functional     – Layer 2
 7. Ranger         – Layer 3
 8. Logs           – Layer 4 (needs run start timestamp from stage 3)
 9. Publish        – junit 'reports/*.xml', publishHTML, archive discovery.json + logs
post { always { cleanup stage (delete smoke_<run_id>* resources); notify } }
```

Triggers: manual, nightly cron, and after an upgrade/deploy job.

---

## 6. Safety rules (shared cluster)
- All created objects named `smoke_<run_id>_…`; teardown always runs; a separate `cleanup` command removes leftovers from crashed runs.
- Never restart/stop services; never modify existing Ranger policies (only create/delete our own).
- Every test has a timeout; one service failing does not stop the others.
- Secrets only via Jenkins credentials / env vars — never in the repo.

---

## 7. Phasing

> **Update (2026-11-18):** this suite now lives in `acceldata-io/odp-smoke-tests` (branch `ODP-8764`) next to an
> existing bash smoke suite, and the Jenkinsfile lives in `acceldata-io/Jenkins` (branch `ODP-8764`). Reuse rather than
> rebuild: Ambari service checks → `ambari-service-checks-smoke.sh`; Ranger test-connection → `11_ranger-plugin-connection-smoke.sh`;
> log validation → `lib/cluster_log_verify.py` (never with `LOG_VERIFY_RESTART=1`); per-service functional basics →
> the numbered `*-smoke.sh` scripts run via pytest (their env vars override `configs/*.env`).


| Phase | Scope | Outcome |
|---|---|---|
| **1** | Framework, discovery, Layer 0, Layer 1 (all services), Layer 4 (all components), Jenkinsfile, reports | Full breadth on day one |
| **2** | Ranger (Layer 3, full), HDFS, YARN/MR, Hive, Kafka (all 3), HBase/Phoenix, ZooKeeper, KMS | Deep coverage of core + security |
| **3** | Spark (all variants), Livy, Trino, Impala, Kudu, Ozone, Atlas, Solr, Knox, Oozie, NiFi, Druid, Pinot | Compute/query/storage engines |
| **4** | Airflow3, Superset, Hue, Zeppelin, JupyterHub, MLflow, SeaTunnel, Schema Registry, Cruise Control, Celeborn, Flink, Sqoop, Trino Gateway | Long tail; "NOT COVERED" list goes to zero |

---

## 7a. Security / topology combinations
See `ODP-Smoke-Test-Config-Matrix.md`: per-endpoint `ClusterProfile` detection (Kerberos/simple/LDAP, TLS, HA, Ranger plugin), identity & connection layers so tests stay mode-agnostic, STD-AUTH / STD-TLS / STD-WIRE suites, and a Jenkins matrix over tagged clusters. Phase 1 includes the detector.

## 8. Open questions
1. ~~Ranger "14 policies"~~ — resolved: Ranger was an example; full per-service case design is in `ODP-Smoke-Test-Cases.md`.
2. Ambari admin and Ranger admin credentials (to be stored in Jenkins credentials).
3. Which Jenkins instance to use, and can its agent SSH to 10.101.11.x?
4. Where should the code live — new repo (recommended, e.g. `odp-smoke-tests`) or inside `odp-ambari`?
5. Which other clusters (with other Kerberos/TLS/HA combinations) can the pipeline target? Is a dedicated QA cluster available for toggling/provisioning?
6. Who owns the earlier `odp-log-validation` tool? Reuse its rules if available.
