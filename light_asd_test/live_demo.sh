#!/usr/bin/env bash
# 实时摄像头+麦克风 Light-ASD 检测界面
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_PY="$HERE/venv/bin/python"
LASD_DIR="$HERE/Light-ASD"

VIDEO_DEV="${VIDEO_DEV:-/dev/video0}"
AUDIO_DEV="${AUDIO_DEV:-hw:0,0}"
CHECKPOINT="${CHECKPOINT:-weight/finetuning_TalkSet.model}"

VIDEO_INDEX="$(echo "$VIDEO_DEV" | grep -o '[0-9]*$')"

cd "$LASD_DIR"
export PATH="$HERE/venv/bin:$HERE/bin:$PATH"
exec "$VENV_PY" live_demo.py \
  --videoIndex "$VIDEO_INDEX" \
  --audioDevice "$AUDIO_DEV" \
  --pretrainModel "$CHECKPOINT"
