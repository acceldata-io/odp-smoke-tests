# ODP Smoke-Test Case Catalog

Companion to `ODP-Smoke-Test-Pipeline-Plan.md`. Designed against cluster rl10test1/2/3 (ODP 3.3.6.6).

---

## 0. How the test cases are designed

Every service is tested on the **same 8 dimensions** (the "Ranger example" generalised to all services):

| Code | Dimension | Question it answers |
|---|---|---|
| **SC** | Ambari service check | Does the vendor-provided check pass? |
| **HL** | Health / availability | Are all processes up, ports listening, REST/UI answering, HA roles correct (1 active + standby)? |
| **FN** | Functional – basic | Can I do the service's core job (write/read, produce/consume, query)? |
| **FM** | Functional – medium | Does it work end-to-end with its dependencies (Hive→Tez→YARN→HDFS, Spark→Hive Metastore, Hive→Atlas lineage…)? |
| **KRB** | Kerberos authentication | Is access **rejected without a ticket** and **accepted with one**? Does the web UI demand SPNEGO (browser Kerberos login)? |
| **RGR** | Ranger authorization | Repo test-connection, policy download, deny→allow→deny enforcement (only where a Ranger plugin exists) |
| **AUD** | Audit | Is the action we performed recorded in Solr `ranger_audits` **and** HDFS `/ranger/audit/<svc>/` and in the service's own audit log? |
| **LOG** | Log generation | Does each component write logs, did our activity show up in them, and no new ERROR/FATAL lines appeared? |

Each case has an ID `<SERVICE>-<DIM>-<NN>` and a tier: **B** = basic (fast, safe, every run) or **M** = medium (multi-step, integration; nightly). IDs become pytest markers so Jenkins can run e.g. `-m "tier_basic and service_hdfs"`.

To avoid writing the same thing 60 times, three **standard suites** (§1) are applied to every service where relevant, and §2 lists only the **service-specific** cases.

### Cluster facts the cases rely on
- Test users with keytabs: `smokeuser`, `newuser_1`, `newuser_2` (`/etc/security/keytabs/*.keytab`) → `newuser_1` is the "unprivileged user" in allow/deny tests.
- Ranger plugins present: HDFS, YARN, HBase, Kafka, Kafka3, Kafka4, Ozone, KMS, Atlas, Schema Registry, Trino. All audit to **both Solr and HDFS**.
- **No Ranger plugin for Hive** (`hive.security.authorization.manager` empty), Impala, Knox, Solr, NiFi, Kudu → their RGR cases are auto-skipped with reason "plugin not enabled" (and reported, not hidden).
- **No Ranger TagSync installed** → tag-based policy *enforcement* is N/A; tag policy CRUD only.
- HDFS HA nameservice `rl10test`, YARN RM HA on, KMS at `rl10test1;rl10test3:9292`, HTTP-only (no TLS).
- Hive: binary transport + ZooKeeper discovery. Kafka/Kafka3: `SASL_PLAINTEXT`/GSSAPI. **Kafka4: `PLAINTEXT` listener (no Kerberos)** → KAFKA4-KRB will fail by design — flagged as a finding.

---

## 1. Standard suites (applied automatically)

### 1.1 STD-SC — Ambari service check (every service)
| ID | Tier | Method | Pass criteria |
|---|---|---|---|
| STD-SC-01 | B | `POST /api/v1/clusters/<c>/requests` `{command: <SVC>_SERVICE_CHECK}`; poll request | status `COMPLETED`, all tasks `COMPLETED`; on failure attach task stderr to report |

### 1.2 STD-HL — Health (every service)
| ID | Tier | Method | Pass criteria |
|---|---|---|---|
| STD-HL-01 | B | Ambari `host_components?HostRoles/state` for every non-client component | `STARTED`, not in maintenance mode |
| STD-HL-02 | B | Ambari alerts for the service | no `CRITICAL`; `WARNING` listed in report |
| STD-HL-03 | B | TCP connect to every port declared in the component's config | port open on the right host |
| STD-HL-04 | B | Process check on host (`pgrep -f` with component main class) | exactly the expected number of JVMs/processes, running as service user |
| STD-HL-05 | B | Client package version (`/usr/odp/current/<comp>` → `3.3.6.6`) on all hosts | matches cluster stack version |

### 1.3 STD-KRB — Kerberos (services exposing RPC/REST)
> **Superseded by STD-AUTH + STD-TLS + STD-WIRE in `ODP-Smoke-Test-Config-Matrix.md`** (mode-aware: Kerberos / simple / LDAP, TLS on/off per endpoint). Kept here for reference.
| ID | Tier | Method | Pass criteria |
|---|---|---|---|
| STD-KRB-01 | B | `kdestroy`; run the service's basic client command | fails with auth error (GSS / 401 / SASL) — **not** a success |
| STD-KRB-02 | B | `kinit -kt smokeuser.headless.keytab`; same command | succeeds |
| STD-KRB-03 | B | `curl` UI/REST without `--negotiate` | `401` |
| STD-KRB-04 | B | `curl --negotiate -u :` | `200` |
| STD-KRB-05 | M | Service keytab principals valid: `klist -kt` + `kinit -kt` for each service keytab on its host | all succeed; keytab not expired; correct owner/permission `400`/`440` |

### 1.4 STD-RGR — Ranger plugin suite (every service with a Ranger plugin)
Uses resource `R` that the service-specific section defines (e.g. HDFS path, Kafka topic) named `smoke_<run_id>_…`.

| ID | Tier | Method | Pass criteria |
|---|---|---|---|
| STD-RGR-01 | B | Ranger Admin: repo for service exists & enabled | repo present, `isEnabled=true` |
| STD-RGR-02 | B | Repo **Test Connection** (same call as UI) | success; lookup returns resources |
| STD-RGR-03 | B | Resource lookup/autocomplete for `R`'s parent | returns non-empty list |
| STD-RGR-04 | B | Ranger plugin-status info for repo | every plugin host listed; last policy download < polling interval × 2; downloaded version == admin's current version |
| STD-RGR-05 | B | Plugin host: `/etc/ranger/<repo>/policycache/*.json` | exists, non-empty, version matches admin |
| STD-RGR-06 | B | As `newuser_1` access `R` (no policy) | **DENIED** |
| STD-RGR-07 | B | Create allow policy (`newuser_1`, `R`, needed perms) → poll STD-RGR-04 until new version activated on all hosts | policy downloaded within timeout (default 60s); cache file updated |
| STD-RGR-08 | B | As `newuser_1` access `R` | **ALLOWED** |
| STD-RGR-09 | M | Add **deny** item for same user/resource in same policy | DENIED (deny beats allow) |
| STD-RGR-10 | M | Add deny **exception** | ALLOWED |
| STD-RGR-11 | M | Group-based policy (group synced by usersync) instead of user | ALLOWED for member, DENIED for `newuser_2` |
| STD-RGR-12 | M | Role-based policy (create Ranger role with `newuser_1`) | ALLOWED |
| STD-RGR-13 | M | Disable policy → wait for download | DENIED |
| STD-RGR-14 | B | Delete policy (cleanup) → wait for download | DENIED; policy gone from admin |
| STD-RGR-15 | M | Policy with validity schedule in the past | DENIED |

### 1.5 STD-AUD — Audit (every service with a Ranger plugin)
| ID | Tier | Method | Pass criteria |
|---|---|---|---|
| STD-AUD-01 | B | Query Solr `ranger_audits` for `resource:*smoke_<run_id>*` and `repo=<repo>` | DENIED event from RGR-06 **and** ALLOWED event from RGR-08 present, correct user, access type, client IP, policy id (for allowed) — within 120s |
| STD-AUD-02 | B | HDFS `/ranger/audit/<svc>/<yyyymmdd>/` | directory exists, a file modified today (HDFS audit flushes periodically → check growth, don't require the exact event) |
| STD-AUD-03 | M | Plugin host audit spool dir (`.../audit/solr/spool`, `.../audit/hdfs/spool`) | no stuck/archived spool files piling up (= destination unreachable) |
| STD-AUD-04 | M | Ranger Admin "Plugin" audit (policy download events) and "Admin" audit (our policy create/delete) | both entries present |

### 1.6 STD-LOG — Log generation (every component on every host)
Log dir is taken from Ambari configs (e.g. `hadoop-env/hdfs_log_dir_prefix`, `hive-env/hive_log_dir`).

| ID | Tier | Method | Pass criteria |
|---|---|---|---|
| STD-LOG-01 | B | Log dir exists | exists, owned by service user, not world-writable |
| STD-LOG-02 | B | Main log file of each component | exists and **size > 0** |
| STD-LOG-03 | B | mtime after run start | component wrote logs during the run |
| STD-LOG-04 | B | Scan lines written since run start for `ERROR|FATAL|Exception` | none, except allowlist (`configs/pipeline/log_error_allowlist.yaml`) |
| STD-LOG-05 | B | **Activity marker**: the `smoke_<run_id>` resource name appears in the relevant server/audit log (defined per service below) | found — proves our action really reached the server and was logged |
| STD-LOG-06 | M | log4j/logback config: rolling appender with max size + backup count | configured; rotated files (`.log.1`, `.log.YYYY-MM-DD`) present if service older than 1 day |
| STD-LOG-07 | M | `.out` / `.err` files | no `OutOfMemoryError`, `Address already in use`, unhandled stack traces |
| STD-LOG-08 | M | GC logs (JVM services) | present; no Full GC > 10s in window |
| STD-LOG-09 | M | Log dir disk usage | < threshold (default 80% of partition; dir < 10 GB) |

---

## 2. Service-specific cases

Notation: "+STD" = all standard suites that apply. Only extra cases are listed.

### 2.1 ZooKeeper (+STD-SC, HL, KRB, LOG)
| ID | Tier | Test | Pass criteria |
|---|---|---|---|
| ZK-HL-01 | B | `srvr`/`ruok` four-letter-word on each server | `imok`; exactly 1 `leader`, rest `follower` |
| ZK-FN-01 | B | `zkCli` create/get/set/delete `/smoke_<run_id>` (SASL) | values round-trip |
| ZK-FN-02 | M | Create on server A, read on server B and C | consistent data across quorum |
| ZK-KRB-01 | M | Znode with `sasl:smokeuser:cdrwa` ACL; read as `newuser_1` | NoAuth error |
| ZK-LOG-05 | B | marker | session establishment for our client in ZK log |

### 2.2 HDFS (+all STD; Ranger resource = `/tmp/smoke_<run_id>/ranger`)
| ID | Tier | Test | Pass criteria |
|---|---|---|---|
| HDFS-HL-01 | B | `hdfs haadmin -getAllServiceState` | exactly 1 active, others standby |
| HDFS-HL-02 | B | `hdfs dfsadmin -safemode get` | OFF |
| HDFS-HL-03 | B | `dfsadmin -report` | live DNs == Ambari DN count; 0 dead; no missing/corrupt blocks |
| HDFS-HL-04 | B | JournalNode quorum (JMX `JournalNode` on each) | all JNs in sync (lag < threshold) |
| HDFS-HL-05 | B | ZKFC each NN host | running, healthy |
| HDFS-FN-01 | B | mkdir/put/cat/ls/mv/chmod/rm | content checksum matches |
| HDFS-FN-02 | B | WebHDFS (`curl --negotiate .../webhdfs/v1/...?op=LISTSTATUS`) | 200, lists our dir |
| HDFS-FN-03 | B | HttpFS (port 14000) same ops | 200 |
| HDFS-FN-04 | M | Large file (256 MB) put → block count/replication check (`fsck -files -blocks`) | replication == `dfs.replication`, HEALTHY |
| HDFS-FM-01 | M | Snapshot: allow, create, modify file, diff, restore, delete | diff shows change; restore OK |
| HDFS-FM-02 | M | Trash: `rm` → file in `.Trash`; `-skipTrash` bypass | as expected |
| HDFS-FM-03 | M | **Encryption zone**: KMS key → `hdfs crypto -createZone` → write/read as smokeuser; read raw bytes via `/.reserved/raw` as hdfs | plaintext readable via normal path, ciphertext differs |
| HDFS-FM-04 | M | Quotas: name & space quota exceeded | `QuotaExceededException` |
| HDFS-FM-05 | M | Erasure coding policy (if enabled) set on dir + write/read | OK |
| HDFS-FM-06 | M | NFS gateway: `showmount -e`, mount, read file written via CLI | content matches |
| HDFS-FM-07 | M | Client failover: run reads while querying via nameservice URI (no failover triggered) | resolves to active NN |
| HDFS-AUD-05 | B | `hdfs-audit.log` on active NN | contains `src=/tmp/smoke_<run_id>` for our ops |

### 2.3 YARN + MapReduce2 (+all STD; Ranger resource = queue `root.default` submit-app for `newuser_1`)
| ID | Tier | Test | Pass criteria |
|---|---|---|---|
| YARN-HL-01 | B | `yarn rmadmin -getAllServiceState` | 1 active RM |
| YARN-HL-02 | B | `yarn node -list -all` | RUNNING NMs == Ambari NM count; 0 UNHEALTHY/LOST |
| YARN-HL-03 | B | Timeline Server v1.5 + Timeline Reader v2 REST | 200 |
| YARN-HL-04 | B | Registry DNS resolves a test record (`dig @<host> -p <port>`) | answers |
| YARN-FN-01 | B | `yarn jar hadoop-mapreduce-examples.jar pi 2 10` | SUCCEEDED |
| YARN-FN-02 | B | wordcount on HDFS input | output matches expected counts |
| YARN-FN-03 | M | DistributedShell app | SUCCEEDED |
| YARN-FM-01 | M | Job History Server REST shows FN-01 job | present, state SUCCEEDED, counters present |
| YARN-FM-02 | M | Log aggregation: `yarn logs -applicationId` | container logs retrievable |
| YARN-FM-03 | M | Queue list & capacity scheduler `yarn queue -status default` | RUNNING |
| YARN-FM-04 | M | Kill app: submit sleep job → `yarn app -kill` | state KILLED |
| YARN-RGR | B | STD-RGR with queue submit permission | `newuser_1` submit denied → allowed |

### 2.4 Tez (+STD-SC, LOG)
| ID | Tier | Test | Pass criteria |
|---|---|---|---|
| TEZ-FN-01 | B | `tez-examples` OrderedWordCount | SUCCEEDED |
| TEZ-FM-01 | M | Tez UI / ATS shows DAG | DAG present |

### 2.5 Hive (HMS, HS2, HSI/LLAP, WebHCat) (+STD-SC, HL, KRB, LOG; RGR auto-skip: plugin not enabled)
| ID | Tier | Test | Pass criteria |
|---|---|---|---|
| HIVE-HL-01 | B | ZK namespace `/hiveserver2` lists all HS2 instances | count == Ambari HS2 count |
| HIVE-HL-02 | B | HMS thrift reachable (`hive --service metatool -listFSRoot` or `SHOW DATABASES`) | OK |
| HIVE-FN-01 | B | beeline (ZK discovery URL, Kerberos) `CREATE DATABASE smoke_<run_id>`; managed ACID table; insert; select; drop | rows match |
| HIVE-FN-02 | B | External table on HDFS path; `MSCK REPAIR`; partition query | partitions recognized |
| HIVE-FN-03 | M | Join + group-by on Tez | correct result; Tez app in YARN |
| HIVE-FM-01 | M | ACID: update/delete/merge + `SHOW COMPACTIONS` after manual compaction | rows updated; compaction initiated/succeeded |
| HIVE-FM-02 | M | Hive Interactive (LLAP) query | succeeds; LLAP daemons in YARN |
| HIVE-FM-03 | M | WebHCat `/templeton/v1/status` + DDL call | `ok` |
| HIVE-FM-04 | M | **Atlas lineage**: CTAS from FN-01 table | Atlas has both `hive_table` entities and a `hive_process` lineage edge within 120s |
| HIVE-FM-05 | M | Each HS2 instance individually (direct URL) | all serve queries |
| HIVE-LOG-05 | B | marker | HS2 log contains our query id / `smoke_<run_id>` |

### 2.6 HBase + Phoenix (+all STD; Ranger resource = table `smoke_<run_id>:t1`)
| ID | Tier | Test | Pass criteria |
|---|---|---|---|
| HBASE-HL-01 | B | `status 'simple'` | 1 active master, backups listed, all RS live, 0 dead |
| HBASE-HL-02 | B | `hbck` (read-only report) | no inconsistencies / RIT |
| HBASE-FN-01 | B | create namespace/table, put, get, scan, delete, disable, drop | values round-trip |
| HBASE-FN-02 | M | Flush + major compact; region split | succeeds; regions count increases |
| HBASE-FN-03 | M | REST/Thrift (if installed) get row | OK |
| HBASE-FM-01 | M | Phoenix (`sqlline.py` / query server): create table, upsert, select, secondary index | rows match; index used |
| HBASE-FM-02 | M | Atlas HBase hook: table appears as `hbase_table` | entity present |
| HBASE-RGR | B | STD-RGR (`newuser_1` read table) | denied → allowed |

### 2.7 Kafka / Kafka3 / Kafka4 (same template, parametrized by variant; Ranger resource = topic `smoke_<run_id>_t`)
| ID | Tier | Test | Pass criteria |
|---|---|---|---|
| KAFKA-HL-01 | B | `kafka-broker-api-versions` / metadata | all brokers registered (ZK for Kafka/Kafka3, KRaft controller quorum for Kafka4) |
| KAFKA-HL-02 | B | Kafka4: `kafka-metadata-quorum describe --status` | leader elected, all voters caught up |
| KAFKA-FN-01 | B | create topic (RF = min(3, brokers)), describe | ISR == RF |
| KAFKA-FN-02 | B | produce 1000 msgs / consume from beginning (SASL GSSAPI client config) | 1000 msgs, content matches |
| KAFKA-FN-03 | M | Consumer group: consume half, describe group lag, resume | offsets committed; lag correct |
| KAFKA-FN-04 | M | Alter partitions, delete topic | applied / topic gone |
| KAFKA-FM-01 | M | Kafka Connect: REST `/connectors`, deploy FileStream source → topic | records arrive |
| KAFKA-FM-02 | M | MirrorMaker2 (if configured) replicate test topic | mirrored topic receives msgs |
| KAFKA-FM-03 | M | Cruise Control (CC3→Kafka3, CC4→Kafka4) `/kafkacruisecontrol/state` & `load` | `RUNNING`, sees all brokers |
| KAFKA-RGR | B | STD-RGR: `newuser_1` produce/consume on topic | denied → allowed |
| KAFKA-KRB | B | STD-KRB | **Kafka4 expected to fail (PLAINTEXT listener) → reported as security finding** |
| KAFKA-LOG-05 | B | marker | topic creation logged in controller/server log |

### 2.8 Schema Registry (+all STD; Ranger resource = schema `smoke_<run_id>`)
| ID | Tier | Test | Pass criteria |
|---|---|---|---|
| SR-FN-01 | B | Register Avro schema, fetch by name/version | round-trip |
| SR-FN-02 | M | Register incompatible version under BACKWARD | rejected |
| SR-FM-01 | M | Kafka produce with schema id + consume/deserialize | OK |

### 2.9 Ranger Admin + UserSync (Ranger itself)
| ID | Tier | Test | Pass criteria |
|---|---|---|---|
| RANGER-HL-01 | B | Admin UI/REST login (each admin host) | 200 |
| RANGER-HL-02 | B | Ranger DB reachable (Admin `/actuator`-style health or simple API call timing) | < 2s |
| RANGER-FN-01 | B | List service-defs | every service type the cluster uses is present; count recorded |
| RANGER-FN-02 | B | **Policy generation per service-def**: for every service-def with a repo, create + read + update + delete one access policy | all succeed (this is the "all supported policy types" coverage) |
| RANGER-FN-03 | M | Masking policy & row-filter policy CRUD on Trino repo (Hive N/A — no plugin) | created; enforcement checked in TRINO-RGR |
| RANGER-FN-04 | M | Tag-based policy CRUD (tag service) | CRUD OK; enforcement N/A (no TagSync) |
| RANGER-FN-05 | M | Security zone CRUD with one resource from HDFS | created/deleted |
| RANGER-FN-06 | M | Roles CRUD; users/groups API | OK |
| RANGER-FN-07 | M | Policy export/import (JSON) of a test repo's policies to file and back into a new test repo | counts match |
| RANGER-US-01 | B | UserSync: `newuser_1`, `newuser_2` and their OS/LDAP groups visible in Ranger | present |
| RANGER-US-02 | M | Add OS user/group on host → wait sync interval | appears in Ranger (cleanup user afterwards) |
| RANGER-AUD-01 | B | Solr `ranger_audits` collection health (Infra Solr) | ACTIVE replicas, docs added today |
| RANGER-AUD-02 | M | Audit search API in Ranger Admin returns events from STD-AUD-01 | same events visible via Ranger UI API |
| RANGER-LOG-05 | B | marker | `ranger-admin-*.log` / access log contains our policy create calls |

### 2.10 Ranger KMS (+all STD; Ranger resource = key `smoke_<run_id>_k`)
| ID | Tier | Test | Pass criteria |
|---|---|---|---|
| KMS-HL-01 | B | Each KMS instance `/kms/v1/keys/names` with SPNEGO | 200 |
| KMS-FN-01 | B | `hadoop key create/list/roll/delete` | key versions increment |
| KMS-FN-02 | M | Same key visible from both KMS instances (HA / shared DB) | consistent |
| KMS-FM-01 | M | Encryption zone (shared with HDFS-FM-03) | read/write OK |
| KMS-RGR | B | STD-RGR: `newuser_1` `getMetadata`/`decrypteek` | denied → allowed |

### 2.11 Atlas (+all STD; Ranger resource = entity-type `hive_table` read for `newuser_1`)
| ID | Tier | Test | Pass criteria |
|---|---|---|---|
| ATLAS-HL-01 | B | `/api/atlas/admin/status` on each server | exactly 1 `ACTIVE`, others `PASSIVE` |
| ATLAS-HL-02 | B | Backing stores: HBase `atlas_janus` table, Solr collections (`vertex_index`, `edge_index`, `fulltext_index`), Kafka topics `ATLAS_HOOK`, `ATLAS_ENTITIES` | all exist & healthy |
| ATLAS-FN-01 | B | Create/search/delete custom entity via REST | round-trip |
| ATLAS-FN-02 | M | Create classification, attach, search by classification | found |
| ATLAS-FM-01 | M | Lineage from HIVE-FM-04 | lineage graph has edge |
| ATLAS-FM-02 | M | Hook consumer lag on `ATLAS_HOOK` | lag ~0 |

### 2.12 Infra Solr / Solr (+STD-SC, HL, KRB, LOG)
| ID | Tier | Test | Pass criteria |
|---|---|---|---|
| SOLR-HL-01 | B | `CLUSTERSTATUS` | all collections, all replicas ACTIVE; live nodes == hosts |
| SOLR-FN-01 | B | create collection (Solr service only), add docs, query, delete | doc count matches |
| SOLR-FN-02 | M | Infra Solr: `ranger_audits`, Atlas collections query | numFound > 0 |

### 2.13 Ozone (+all STD; Ranger resource = volume `smoke<run_id>`)
| ID | Tier | Test | Pass criteria |
|---|---|---|---|
| OZONE-HL-01 | B | `ozone admin om roles` / `scm roles` | 1 leader each, followers listed |
| OZONE-HL-02 | B | `ozone admin datanode list` / `safemode status` | all DNs HEALTHY; safemode off |
| OZONE-FN-01 | B | `ozone sh volume/bucket/key` create/put/get/delete | checksum matches |
| OZONE-FN-02 | M | ofs:// via `hdfs dfs` | ls/put/get OK |
| OZONE-FN-03 | M | S3 Gateway: `ozone s3 getsecret` + `aws s3 --endpoint-url` mb/cp/ls/rm | OK |
| OZONE-FM-01 | M | Recon REST `/api/v1/clusterState` | DN/container counts match |
| OZONE-RGR | B | STD-RGR: `newuser_1` create key in bucket | denied → allowed |

### 2.14 Spark (Spark2, Spark3, Spark3_3_3_3, Spark3_3_5_1, Spark4 — one template, parametrized; + Spark-RAPIDS jars)
| ID | Tier | Test | Pass criteria |
|---|---|---|---|
| SPARK-FN-01 | B | `spark-submit --master yarn --deploy-mode cluster SparkPi` | exit 0, "Pi is roughly" in driver log |
| SPARK-FN-02 | B | `spark-sql`/pyspark: read HDFS CSV → write Parquet → read back | row counts equal |
| SPARK-FM-01 | M | Spark SQL against Hive Metastore: query table from HIVE-FN-01, write new table | visible from beeline |
| SPARK-FM-02 | M | History Server REST `/api/v1/applications` contains FN-01 app | present, completed |
| SPARK-FM-03 | M | Thrift Server: beeline (Kerberos) `SELECT` | OK |
| SPARK-FM-04 | M | Spark Connect (Spark3/4): pyspark remote session simple DF op | OK |
| SPARK-FM-05 | M | Dynamic allocation / external shuffle (or Celeborn remote shuffle when configured) with a shuffle-heavy job | succeeds; Celeborn master shows app if used |
| SPARK-FM-06 | M | Spark-RAPIDS jar loads (`--conf spark.plugins=com.nvidia.spark.SQLPlugin`) — skip if no GPU | plugin initialises or skipped with reason |
| SPARK-FM-07 | M | Spark-Atlas hook (if configured) | Atlas entity for Spark output |

### 2.15 Livy (all 5 variants, parametrized)
| ID | Tier | Test | Pass criteria |
|---|---|---|---|
| LIVY-HL-01 | B | `GET /sessions` with SPNEGO | 200 |
| LIVY-FN-01 | B | Batch: POST `/batches` SparkPi | state `success` |
| LIVY-FN-02 | M | Interactive session (pyspark), run statement, delete session | output correct |
| LIVY-FN-03 | M | Impersonation: `proxyUser=newuser_1` → YARN app owner | owner == newuser_1 |

### 2.16 Trino + Trino Gateway (+all STD; Ranger resource = `hive.smoke_<run_id>.t1`)
| ID | Tier | Test | Pass criteria |
|---|---|---|---|
| TRINO-HL-01 | B | `/v1/info` (starting=false), `/v1/node` | all workers registered |
| TRINO-FN-01 | B | `SELECT 1`; `SHOW CATALOGS` | OK; expected catalogs present |
| TRINO-FN-02 | B | Query Hive table from HIVE-FN-01; CTAS into Hive catalog | rows match |
| TRINO-FM-01 | M | Other catalogs configured (Kudu/Iceberg/Kafka/…): one query each | OK |
| TRINO-FM-02 | M | Through Trino Gateway URL | routed to cluster; result identical |
| TRINO-RGR | B | STD-RGR (select on table) | denied → allowed |
| TRINO-RGR-M1 | M | **Masking** policy on column for `newuser_1` | value masked |
| TRINO-RGR-M2 | M | **Row-filter** policy | only filtered rows returned |

### 2.17 Impala + Kudu (+STD-SC, HL, KRB, LOG)
| ID | Tier | Test | Pass criteria |
|---|---|---|---|
| IMPALA-HL-01 | B | statestored/catalogd/impalad web UIs (25010/25020/25000) | 200; all impalads registered in statestore |
| IMPALA-FN-01 | B | `impala-shell -k`: `INVALIDATE METADATA` + query HIVE-FN-01 table | rows match |
| IMPALA-FN-02 | M | Create Parquet table, insert, compute stats | OK |
| KUDU-HL-01 | B | `kudu cluster ksck <masters>` | OK, all tservers healthy |
| KUDU-FN-01 | B | Impala: create Kudu table, insert/upsert/delete, select | rows match |

### 2.18 Druid (+STD-SC, HL, KRB, LOG)
| ID | Tier | Test | Pass criteria |
|---|---|---|---|
| DRUID-HL-01 | B | `/status/health` on coordinator, overlord, broker, router, historical, middlemanager | `true` |
| DRUID-FN-01 | M | Native batch ingestion of 100-row inline dataset `smoke_<run_id>` | task SUCCESS, segment loaded |
| DRUID-FN-02 | M | SQL `/druid/v2/sql` count(*) | 100 |
| DRUID-FN-03 | M | Drop datasource (cleanup) | removed |

### 2.19 Pinot (+STD-SC, HL, LOG)
| ID | Tier | Test | Pass criteria |
|---|---|---|---|
| PINOT-HL-01 | B | controller/broker/server/minion `/health` | OK |
| PINOT-FN-01 | M | Create schema+offline table, upload small segment, query via broker | row count matches |

### 2.20 Oozie (+STD-SC, HL, KRB, LOG)
| ID | Tier | Test | Pass criteria |
|---|---|---|---|
| OOZIE-HL-01 | B | `oozie admin -status` | NORMAL |
| OOZIE-FN-01 | M | Shell-action workflow from HDFS | SUCCEEDED |
| OOZIE-FN-02 | M | Hive2 action (beeline) workflow | SUCCEEDED |

### 2.21 Sqoop / Pig
| ID | Tier | Test | Pass criteria |
|---|---|---|---|
| SQOOP-FN-01 | B | `sqoop list-databases` against cluster MySQL (read-only test user) | returns list |
| SQOOP-FN-02 | M | Import small table to HDFS | row count matches |
| PIG-FN-01 | B | Pig script (tez mode) load/filter/store | output matches |

### 2.22 Knox (+STD-SC, HL, LOG; RGR auto-skip)
| ID | Tier | Test | Pass criteria |
|---|---|---|---|
| KNOX-FN-01 | B | WebHDFS via topology `LISTSTATUS` | 200 |
| KNOX-FN-02 | M | Hive (HTTP via Knox) & YARN RM UI proxies defined in topology | 200 |
| KNOX-FN-03 | M | Bad credentials | 401 |

### 2.23 NiFi2 + NiFi Registry2 (+STD-SC, HL, KRB, LOG)
| ID | Tier | Test | Pass criteria |
|---|---|---|---|
| NIFI-HL-01 | B | `/nifi-api/flow/cluster/summary` | all nodes CONNECTED |
| NIFI-FN-01 | M | Create process group with GenerateFlowFile→PutHDFS(`/tmp/smoke_<run_id>/nifi`), start, stop, delete | files land in HDFS |
| NIFIREG-FN-01 | M | Create bucket, version the test process group, delete | version 1 exists |

### 2.24 Flink / Celeborn / SeaTunnel
| ID | Tier | Test | Pass criteria |
|---|---|---|---|
| FLINK-FN-01 | B | `flink run -m yarn-cluster` WordCount example | FINISHED |
| FLINK-FM-01 | M | History Server shows job | present |
| CELEB-HL-01 | B | Master HA group & worker list (REST) | leader + all workers |
| CELEB-FN-01 | M | Spark job with Celeborn shuffle manager | succeeds; shuffle data seen on workers |
| SEAT-HL-01 | B | Zeta master/worker REST overview | all workers registered |
| SEAT-FN-01 | M | FakeSource→Console (or →HDFS) batch job | FINISHED |

### 2.25 UI / Notebook / Orchestration services (Airflow3, Superset, Hue, Zeppelin, JupyterHub, MLflow)
| ID | Tier | Test | Pass criteria |
|---|---|---|---|
| AIRFLOW-HL-01 | B | API health: scheduler, dag-processor, triggerer heartbeat fresh; workers registered | healthy |
| AIRFLOW-FN-01 | M | Deploy `smoke_<run_id>` DAG (BashOperator) → trigger → wait | run `success` |
| SUPERSET-HL-01 | B | `/health` | OK |
| SUPERSET-FN-01 | M | Login (API), list databases, run SQL Lab `SELECT 1` against Trino/Hive DB | OK |
| HUE-HL-01 | B | `/desktop/debug/is_alive` | 200 |
| HUE-FN-01 | M | Login + Hive editor query via API | OK |
| ZEPPELIN-HL-01 | B | `/api/version` | 200 |
| ZEPPELIN-FN-01 | M | Create note with `%sh`/`%spark` paragraph, run, delete | FINISHED |
| JHUB-HL-01 | B | `/hub/health` | 200 |
| MLFLOW-HL-01 | B | `/health` | OK |
| MLFLOW-FN-01 | M | Create experiment, log run param/metric, read back, delete | values match |

### 2.26 Kerberos / Cluster-wide
| ID | Tier | Test | Pass criteria |
|---|---|---|---|
| CLUSTER-01 | B | KDC reachable from every host; `kinit` smokeuser on each host | OK |
| CLUSTER-02 | B | Time skew between hosts (chrony) | < 1s (Kerberos breaks at 5 min) |
| CLUSTER-03 | B | Ambari agents heartbeat; agent/server version match | OK |
| CLUSTER-04 | B | Disk free on `/`, `/var/log`, data dirs | > 20% |
| CLUSTER-05 | M | Reverse DNS / `hostname -f` consistency vs Ambari host names | match |

---

## 3. Execution order & dependencies
```
CLUSTER → ZK → HDFS → KMS → YARN/MR → Tez → Hive → HBase/Phoenix → Kafka* → SchemaRegistry → Solr/InfraSolr
        → Ranger (needs all plugins' resources) → Atlas (needs Hive/HBase activity) → Ozone → Spark* → Livy*
        → Trino/Impala/Kudu → Druid/Pinot → Oozie/Sqoop/Pig → Knox → NiFi → Flink/Celeborn/SeaTunnel → UIs
        → LOG suite last (needs all activity to have happened)
```
If a base service fails its HL tests, dependents are marked **BLOCKED** (not FAILED) so the report points at the root cause.

## 4. Report shape
Per service a row: `SC | HL | FN | FM | KRB | RGR | AUD | LOG` each = PASS / FAIL / SKIP(reason) / BLOCKED / N/A, plus drill-down to each case with command, output and timings.
