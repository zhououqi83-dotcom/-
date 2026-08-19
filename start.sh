#!/usr/bin/env bash
# 一键启动 gaze_arbiter 全套服务:
#   1) head_grpc_server.py —— 头部舵机 gRPC 服务(后台常驻)
#   2) web_dashboard.py    —— 网页仪表盘(前台, Ctrl+C 停止, 会自动带走 1)
#
# 依赖装好用 install_conda_env.sh(装进 conda 环境 servo_face)。
#
# 用法: bash start.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_NAME="servo_face"
LOG_DIR="${ROOT}/.run_logs"
mkdir -p "${LOG_DIR}"

if ! command -v conda >/dev/null 2>&1; then
  echo "没找到 conda, 先跑 install_conda_env.sh 装环境。"
  exit 1
fi
# shellcheck disable=SC1091
source "$(conda info --base)/etc/profile.d/conda.sh"
conda activate "${ENV_NAME}"

# ── 1) 头部舵机 gRPC 服务(后台) ────────────────────────────────────────
echo ">> 启动头部舵机服务 (head_grpc_server.py) ..."
python "${ROOT}/servo_tuning/head-sdk-face/head-server/src/head_grpc_server.py" \
  --config "${ROOT}/servo_tuning/head-sdk-face/head-server/src/servoConfig_25DV3_Ula.yaml" \
  > "${LOG_DIR}/head_grpc_server.log" 2>&1 &
HEAD_PID=$!

cleanup() {
  echo ""
  echo ">> 停止头部舵机服务 (PID ${HEAD_PID}) ..."
  kill "${HEAD_PID}" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

echo -n ">> 等待头部舵机服务就绪(端口 2543) "
READY=0
for _ in $(seq 1 15); do
  if (exec 3<>"/dev/tcp/127.0.0.1/2543") 2>/dev/null; then
    exec 3<&- 3>&-
    READY=1
    break
  fi
  echo -n "."
  sleep 1
done
echo ""
if [ "${READY}" -eq 1 ]; then
  echo ">> 头部舵机服务已就绪"
else
  echo ">> 警告: 15 秒内没等到头部舵机服务起来, 看 ${LOG_DIR}/head_grpc_server.log 排查; 继续往下启动网页(机器人这块到时候在页面上点"连接机器人"会报错)"
fi

# ── 2) 探测这次开机的麦克风声卡编号 ───────────────────────────────────
# sof-hda-dsp 是这台机器内置麦克风, 每次重启内核分配的 card 编号可能不一样
# (踩过好几次坑了), 这里现测现用, 不写死。
AUDIO_CARD="$(arecord -l 2>/dev/null | grep -m1 "sof-hda-dsp" | sed -n 's/^card \([0-9]\+\).*/\1/p')"
if [ -n "${AUDIO_CARD}" ]; then
  export AUDIO_DEV="hw:${AUDIO_CARD},0"
  echo ">> 检测到内置麦克风: card ${AUDIO_CARD} -> AUDIO_DEV=${AUDIO_DEV}"
else
  echo ">> 警告: 没找到 sof-hda-dsp 声卡, 沿用脚本默认值, 建议手动跑一下 arecord -l 核对"
fi

export HEAD_HOST="${HEAD_HOST:-127.0.0.1}"

# ── 3) 网页仪表盘(前台) ───────────────────────────────────────────────
echo ">> 启动网页仪表盘 ..."
echo ">> 打开浏览器访问: http://localhost:8642/ (局域网内其他设备用这台机器的 IP)"
echo ">> Ctrl+C 停止(会自动一起停掉头部舵机服务)"
python "${ROOT}/gaze_arbiter/examples/web_dashboard.py"
