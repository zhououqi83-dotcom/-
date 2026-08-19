#!/usr/bin/env bash

set -euo pipefail

# gRPC honors http_proxy/https_proxy even for 127.0.0.1 targets (its no_proxy
# parser doesn't understand glob patterns like "127.*"), so a system proxy
# can silently break the Hub's local gRPC connection to HeadSDK. Never proxy this.
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CORE_DIR="${SCRIPT_DIR}"
VIEWER_DIR="${CORE_DIR}/viewer"
DIGITAL_DIR="${VIEWER_DIR}/digital"
CONFIG_PATH="${CORE_DIR}/server_utils/config.yaml"
SESSION_NAME="${TMUX_SESSION_NAME:-talkinghead-core}"
START_VIEWER=1
CURRENT_CONDA_ENV="${CONDA_DEFAULT_ENV:-}"
CURRENT_CONDA_PREFIX="${CONDA_PREFIX:-}"
CURRENT_CONDA_EXE="${CONDA_EXE:-}"

if [[ -z "${CURRENT_CONDA_EXE}" ]]; then
    CURRENT_CONDA_EXE="$(type -P conda || true)"
fi

for arg in "$@"; do
    case "${arg}" in
        --backend-only|--no-digital)
            START_VIEWER=0
            ;;
        --with-digital)
            START_VIEWER=1
            ;;
        *)
            echo "unknown argument: ${arg}" >&2
            echo "usage: ./run.sh [--backend-only|--no-digital|--with-digital]" >&2
            exit 1
            ;;
    esac
done

if [[ ! -f "${CORE_DIR}/main.py" ]]; then
    echo "missing ${CORE_DIR}/main.py" >&2
    exit 1
fi

if [[ ! -f "${DIGITAL_DIR}/package.json" ]]; then
    echo "missing ${DIGITAL_DIR}/package.json" >&2
    exit 1
fi

resolve_hub_base_url() {
    python3 - "${CONFIG_PATH}" <<'PY'
from pathlib import Path
import sys

config_path = Path(sys.argv[1]).resolve()
core_dir = config_path.parent.parent
if str(core_dir) not in sys.path:
    sys.path.insert(0, str(core_dir))

from server_utils.simple_yaml import parse_simple_yaml

config = parse_simple_yaml(config_path.read_text(encoding="utf-8"))
hub = config.get("hub", {})
public_url = str(hub.get("publicUrl") or "").strip().rstrip("/")
host = str(hub.get("host") or "127.0.0.1").strip()
port = int(hub.get("port") or 8765)
print(public_url or f"http://{host}:{port}")
PY
}

is_hub_running() {
    local base_url="${1%/}"
    python3 - "${base_url}" <<'PY'
import json
import sys
import urllib.request

url = sys.argv[1].rstrip("/") + "/status"
try:
    with urllib.request.urlopen(url, timeout=1.5) as response:
        payload = json.loads(response.read().decode("utf-8"))
    raise SystemExit(0 if payload.get("ok") else 1)
except Exception:
    raise SystemExit(1)
PY
}

can_use_tmux() {
    command -v tmux >/dev/null 2>&1 || return 1
    [[ -t 0 && -t 1 ]] || return 1
    [[ -n "${TERM:-}" ]] || return 1
}

quote_shell_arg() {
    printf '%q' "$1"
}

build_tmux_prefix_cmd() {
    local workdir="$1"
    local path_q workdir_q env_q prefix_q exe_q

    path_q="$(quote_shell_arg "${PATH}")"
    workdir_q="$(quote_shell_arg "${workdir}")"

    local cmd="export PATH=${path_q}"

    if [[ -n "${CURRENT_CONDA_ENV}" ]]; then
        env_q="$(quote_shell_arg "${CURRENT_CONDA_ENV}")"
        cmd+=" CONDA_DEFAULT_ENV=${env_q}"
    fi

    if [[ -n "${CURRENT_CONDA_PREFIX}" ]]; then
        prefix_q="$(quote_shell_arg "${CURRENT_CONDA_PREFIX}")"
        cmd+=" CONDA_PREFIX=${prefix_q}"
    fi

    if [[ -n "${CURRENT_CONDA_EXE}" ]]; then
        exe_q="$(quote_shell_arg "${CURRENT_CONDA_EXE}")"
        cmd+=" CONDA_EXE=${exe_q}"
    fi

    cmd+=" && cd ${workdir_q}"
    printf '%s' "${cmd}"
}

build_tmux_run_cmd() {
    local workdir="$1"
    shift

    local cmd
    cmd="$(build_tmux_prefix_cmd "${workdir}")"

    if [[ -n "${CURRENT_CONDA_ENV}" && -n "${CURRENT_CONDA_EXE}" ]]; then
        local env_q exe_q
        env_q="$(quote_shell_arg "${CURRENT_CONDA_ENV}")"
        exe_q="$(quote_shell_arg "${CURRENT_CONDA_EXE}")"
        cmd+=" && ${exe_q} run -n ${env_q}"
    else
        cmd+=" &&"
    fi

    local arg arg_q
    for arg in "$@"; do
        arg_q="$(quote_shell_arg "${arg}")"
        cmd+=" ${arg_q}"
    done

    printf '%s' "${cmd}"
}

HUB_BASE_URL="$(resolve_hub_base_url)"
BACKEND_ALREADY_RUNNING=0
if is_hub_running "${HUB_BASE_URL}"; then
    BACKEND_ALREADY_RUNNING=1
fi

run_backend_cmd() {
    if [[ "${BACKEND_ALREADY_RUNNING}" -eq 1 ]]; then
        echo "hub already running at ${HUB_BASE_URL}, skip backend start"
        return 0
    fi
    if [[ -n "${CURRENT_CONDA_ENV}" && -n "${CURRENT_CONDA_EXE}" ]]; then
        "${CURRENT_CONDA_EXE}" run -n "${CURRENT_CONDA_ENV}" python3 main.py
        return
    fi
    python3 main.py
}

run_view_cmd() {
    if [[ -n "${CURRENT_CONDA_ENV}" && -n "${CURRENT_CONDA_EXE}" ]]; then
        "${CURRENT_CONDA_EXE}" run -n "${CURRENT_CONDA_ENV}" npm run remote:view
        return
    fi
    npm run remote:view
}

if [[ "${START_VIEWER}" -eq 0 ]]; then
    cd "${CORE_DIR}"
    run_backend_cmd
    exit 0
fi

if can_use_tmux; then
    BACKEND_TMUX_CMD="$(build_tmux_run_cmd "${CORE_DIR}" python3 main.py)"
    VIEWER_TMUX_CMD="$(build_tmux_run_cmd "${DIGITAL_DIR}" npm run remote:view)"
    BACKEND_SKIP_TMUX_CMD="$(build_tmux_prefix_cmd "${CORE_DIR}") && echo \"hub already running at ${HUB_BASE_URL}, skip backend start\""

    if tmux has-session -t "${SESSION_NAME}" 2>/dev/null; then
        exec tmux attach-session -t "${SESSION_NAME}"
    fi

    tmux new-session -d -s "${SESSION_NAME}" -c "${CORE_DIR}"
    if [[ "${BACKEND_ALREADY_RUNNING}" -eq 1 ]]; then
        tmux send-keys -t "${SESSION_NAME}:0.0" "${BACKEND_SKIP_TMUX_CMD}" C-m
    else
        tmux send-keys -t "${SESSION_NAME}:0.0" "${BACKEND_TMUX_CMD}" C-m
    fi
    tmux split-window -h -t "${SESSION_NAME}:0.0" -c "${DIGITAL_DIR}"
    tmux send-keys -t "${SESSION_NAME}:0.1" "${VIEWER_TMUX_CMD}" C-m
    tmux select-layout -t "${SESSION_NAME}:0" even-horizontal
    exec tmux attach-session -t "${SESSION_NAME}"
fi

if [[ "${BACKEND_ALREADY_RUNNING}" -eq 0 ]]; then
    cd "${CORE_DIR}"
    run_backend_cmd &
    BACKEND_PID=$!
    cleanup() {
        kill "${BACKEND_PID}" >/dev/null 2>&1 || true
    }
    trap cleanup EXIT INT TERM
else
    echo "hub already running at ${HUB_BASE_URL}, start viewer only"
fi

cd "${DIGITAL_DIR}"
run_view_cmd
