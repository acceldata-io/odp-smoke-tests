#!/usr/bin/env bash
# Runs ON the executor node as root (called by run_smoke.sh). Usage: remote_run.sh <run_dir> [pytest args...]
# Picks the cluster's python3 >= 3.8, creates/reuses a venv, then runs pytest. Exit code = pytest's.
set -euo pipefail
RUN_DIR="$1"; shift
cd "$RUN_DIR"

STATE=/var/lib/odp-smoke
mkdir -p "$STATE"

# Use the cluster's own Python: newest python3 >= 3.8 (clusters ship 3.11 / 3.12). Python 2 and 3.6 are skipped.
# The venv below only holds our test libraries, so the system Python that Ambari's agent uses is never modified.
PY=""
for cand in python3.13 python3.12 python3.11 python3.10 python3.9 python3.8 python3; do
  p="$(command -v "$cand" 2>/dev/null || true)"
  if [ -n "$p" ] && "$p" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)' 2>/dev/null; then
    PY="$p"; break
  fi
done
if [ -z "$PY" ]; then
  echo "ERROR: no python3 >= 3.8 on $(hostname -f) (found only Python 2 / 3.6?). Install python3.11 or python3.12 and re-run."
  exit 3
fi
echo "==> using $("$PY" -V 2>&1) ($PY)"

# venv keyed by interpreter + requirements, reused across runs
KEY="$( (echo "$PY"; "$PY" -V 2>&1; cat requirements.txt) | sha256sum | cut -c1-12)"
VENV="$STATE/venv-$KEY"
if [ ! -f "$VENV/.complete" ]; then
  echo "==> creating venv $VENV"
  rm -rf "$VENV"
  "$PY" -m venv "$VENV"
  "$VENV/bin/pip" install -q --disable-pip-version-check -r requirements.txt
  touch "$VENV/.complete"
fi

# root-owned copy of the key for ssh to the other nodes
install -m 400 .ssh_key .ssh_key_root

export SMOKE_ENV_FILE="$RUN_DIR/.smoke.env"
export SMOKE_SSH_KEY="$RUN_DIR/.ssh_key_root"
export SMOKE_REPORT_DIR="$RUN_DIR/reports"
mkdir -p reports

set +e
"$VENV/bin/python" -m pytest -p no:cacheprovider "$@" 2>&1 | tee reports/pytest.log
rc=${PIPESTATUS[0]}
set -e

chown -R "${SUDO_USER:-root}" reports
exit "$rc"
