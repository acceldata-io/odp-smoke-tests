#!/usr/bin/env bash
# Runs on the Jenkins agent (or your laptop). Copies this suite to the executor node (the FIRST IP,
# normally the Ambari server), runs it there as root, copies the reports back, and cleans up.
#
# Secrets are read from the environment (AMBARI_USER, AMBARI_PASSWORD, RANGER_USER, RANGER_PASSWORD)
# and sent to the executor over ssh STDIN into a 0600 file — never on a command line.
#
# Example (from your Mac):
#   export AMBARI_PASSWORD=... RANGER_PASSWORD=...
#   bin/run_smoke.sh --ips 10.101.11.82,10.101.11.89,10.101.11.114 --key ~/downloads/basa_us_dc.pem
#
# Exit code: 0 all passed, 1 some tests failed, anything else = the run itself broke.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
IPS=""; IPS_FILE=""; KEY=""; SSH_USER="acceldata"; SSH_PORT="22"
TIER="basic"; SERVICES=""; AMBARI_URL="${AMBARI_URL:-}"; RUN_ID=""; REPORT_DIR="$ROOT/reports"
PYTEST_ARGS=()

usage() { sed -n '2,13p' "$0"; echo "Options: --ips a,b,c | --ips-file f  --key f  [--user u] [--port p] [--tier basic|medium]"
          echo "         [--services HDFS,HIVE] [--ambari-url URL] [--run-id id] [--report-dir d] [-- extra pytest args]"; exit 2; }

while [ $# -gt 0 ]; do
  case "$1" in
    --ips) IPS="$2"; shift 2 ;;
    --ips-file) IPS_FILE="$2"; shift 2 ;;
    --key) KEY="$2"; shift 2 ;;
    --user) SSH_USER="$2"; shift 2 ;;
    --port) SSH_PORT="$2"; shift 2 ;;
    --tier) TIER="$2"; shift 2 ;;
    --services) SERVICES="$2"; shift 2 ;;
    --ambari-url) AMBARI_URL="$2"; shift 2 ;;
    --run-id) RUN_ID="$2"; shift 2 ;;
    --report-dir) REPORT_DIR="$2"; shift 2 ;;
    --) shift; PYTEST_ARGS=("$@"); break ;;
    -h|--help) usage ;;
    *) echo "unknown option: $1"; usage ;;
  esac
done

[ -n "$IPS_FILE" ] && IPS="$(cat "$IPS_FILE")"
# one-per-line / spaces / commas -> "a,b,c"
IPS="$(printf '%s' "$IPS" | tr '\r\n\t ' ',,,,' | tr -s ',' | sed 's/^,//; s/,$//')"
[ -n "$IPS" ] || { echo "ERROR: no cluster IPs given (--ips / --ips-file)"; exit 2; }
[ -f "$KEY" ] || { echo "ERROR: ssh key file not found (--key)"; exit 2; }
case "$TIER" in basic|medium) ;; *) echo "ERROR: --tier must be basic or medium"; exit 2 ;; esac

RUN_ID="$(printf '%s' "${RUN_ID:-local-$(date +%Y%m%d-%H%M%S)}" | tr -c 'A-Za-z0-9_.-' '_')"
EXECUTOR="${IPS%%,*}"
REMOTE_DIR="/tmp/odp-smoke-$RUN_ID"
SSH=(ssh -i "$KEY" -p "$SSH_PORT" -o BatchMode=yes -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null
     -o LogLevel=ERROR -o ConnectTimeout=15 -o ServerAliveInterval=30 "$SSH_USER@$EXECUTOR")

echo "==> run_id=$RUN_ID executor=$EXECUTOR nodes=$IPS tier=$TIER services=${SERVICES:-ALL}"

cleanup() { "${SSH[@]}" "sudo -n rm -rf '$REMOTE_DIR'" >/dev/null 2>&1 || true; }
trap cleanup EXIT

"${SSH[@]}" "rm -rf '$REMOTE_DIR' && mkdir -p '$REMOTE_DIR' && chmod 700 '$REMOTE_DIR'"

echo "==> copying suite to $EXECUTOR:$REMOTE_DIR"
TAR_OPTS=()
tar --version 2>/dev/null | grep -q bsdtar && TAR_OPTS=(--no-xattrs --no-mac-metadata)   # macOS: skip Apple xattrs
COPYFILE_DISABLE=1 tar ${TAR_OPTS[@]+"${TAR_OPTS[@]}"} -C "$ROOT" -cf - --exclude=./reports --exclude=./.git --exclude=./.venv \
    --exclude='__pycache__' --exclude=.pytest_cache . | "${SSH[@]}" "tar -xf - -C '$REMOTE_DIR'"

# ssh key + secrets via stdin (not argv) into 0600 files
"${SSH[@]}" "umask 077; cat > '$REMOTE_DIR/.ssh_key'" < "$KEY"
{
  printf 'SMOKE_IPS=%s\n' "$IPS"
  printf 'SMOKE_SSH_USER=%s\n' "$SSH_USER"
  printf 'SMOKE_SSH_PORT=%s\n' "$SSH_PORT"
  printf 'SMOKE_TIER=%s\n' "$TIER"
  printf 'SMOKE_SERVICES=%s\n' "$SERVICES"
  printf 'SMOKE_RUN_ID=%s\n' "$RUN_ID"
  printf 'AMBARI_URL=%s\n' "$AMBARI_URL"
  printf 'AMBARI_USER=%s\n' "${AMBARI_USER:-admin}"
  printf 'AMBARI_PASSWORD=%s\n' "${AMBARI_PASSWORD:-}"
  printf 'RANGER_USER=%s\n' "${RANGER_USER:-admin}"
  printf 'RANGER_PASSWORD=%s\n' "${RANGER_PASSWORD:-}"
} | "${SSH[@]}" "umask 077; cat > '$REMOTE_DIR/.smoke.env'"

QUOTED_ARGS=""
for a in ${PYTEST_ARGS[@]+"${PYTEST_ARGS[@]}"}; do QUOTED_ARGS+=" $(printf '%q' "$a")"; done

echo "==> running tests on $EXECUTOR"
rc=0
"${SSH[@]}" "sudo -n bash '$REMOTE_DIR/bin/remote_run.sh' '$REMOTE_DIR'$QUOTED_ARGS" || rc=$?

echo "==> fetching reports into $REPORT_DIR"
mkdir -p "$REPORT_DIR"
rm -rf "$REPORT_DIR/allure-results" "$REPORT_DIR"/*.json "$REPORT_DIR"/*.xml "$REPORT_DIR"/*.log
"${SSH[@]}" "tar -C '$REMOTE_DIR/reports' -cf - ." | tar -xf - -C "$REPORT_DIR" || echo "WARN: could not fetch reports"

echo "==> done (exit $rc)"
exit "$rc"
