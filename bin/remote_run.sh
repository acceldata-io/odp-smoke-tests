#!/usr/bin/env bash
# Runs ON the executor node as root (called by run_smoke.sh). Usage: remote_run.sh <run_dir> [pytest args...]
# Picks/installs a Python >= 3.8, creates/reuses a venv, then runs pytest. Exit code = pytest's.
set -euo pipefail
RUN_DIR="$1"; shift
cd "$RUN_DIR"

STATE=/var/lib/odp-smoke
mkdir -p "$STATE"

# Python for the test runner (never touches the cluster's own Python):
#  1. newest system python3 >= 3.8 (clusters ship 3.11 / 3.12; RHEL 8 default 3.6 and python2 are not usable)
#  2. else a private Python 3.12 installed with uv into $STATE/python (needs GitHub access;
#     disable with SMOKE_ALLOW_PYTHON_DOWNLOAD=0, force with SMOKE_FORCE_PYTHON_DOWNLOAD=1)
PY=""
if [ "${SMOKE_FORCE_PYTHON_DOWNLOAD:-0}" != 1 ]; then
  for cand in python3.13 python3.12 python3.11 python3.10 python3.9 python3.8 python3; do
    p="$(command -v "$cand" 2>/dev/null || true)"
    if [ -n "$p" ] && "$p" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)' 2>/dev/null; then
      PY="$p"; break
    fi
  done
fi
if [ -z "$PY" ]; then
  if [ "${SMOKE_ALLOW_PYTHON_DOWNLOAD:-1}" != 1 ]; then
    echo "ERROR: no python3 >= 3.8 on executor and download disabled (RHEL 8: dnf install python3.11)"; exit 3
  fi
  UV="$STATE/uv/uv"
  if [ ! -x "$UV" ]; then
    echo "==> no python3 >= 3.8 found; downloading uv to install a private Python 3.12"
    mkdir -p "$STATE/uv"
    curl -fsSL --retry 3 -o "$STATE/uv/uv.tgz" \
      "https://github.com/astral-sh/uv/releases/latest/download/uv-$(uname -m)-unknown-linux-gnu.tar.gz"
    tar -xzf "$STATE/uv/uv.tgz" -C "$STATE/uv" --strip-components=1 && rm -f "$STATE/uv/uv.tgz"
  fi
  export UV_PYTHON_INSTALL_DIR="$STATE/python" UV_CACHE_DIR="$STATE/uv-cache" UV_PYTHON_PREFERENCE=only-managed
  "$UV" python install -q 3.12
  PY="$("$UV" python find 3.12)"
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
