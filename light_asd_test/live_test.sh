#!/usr/bin/env bash
# 摄像头+麦克风实时录制 -> Light-ASD (TalkSet权重) 推理测试平台
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_PY="$HERE/venv/bin/python"
FFMPEG="$HERE/bin/ffmpeg"
LASD_DIR="$HERE/Light-ASD"

DURATION="${1:-15}"                 # 录制时长(秒)，默认15秒
VIDEO_DEV="${VIDEO_DEV:-/dev/video0}"
AUDIO_DEV="${AUDIO_DEV:-hw:0,0}"     # 内置麦克风；如需换成阵列麦克风改这个环境变量
CHECKPOINT="${CHECKPOINT:-weight/finetuning_TalkSet.model}"  # 默认用TalkSet权重(更贴近真实场景)

TS="$(date +%Y%m%d_%H%M%S)"
NAME="live_${TS}"
OUT_MP4="$LASD_DIR/demo/${NAME}.mp4"

mkdir -p "$LASD_DIR/demo"

VIDEO_INDEX="$(echo "$VIDEO_DEV" | grep -o '[0-9]*$')"

echo "=================================================="
echo " 打开摄像头预览，调整好位置后按【空格】开始录制"
echo " (按 Q / ESC 取消)"
echo "=================================================="
if ! "$VENV_PY" "$HERE/preview.py" "$VIDEO_INDEX"; then
  echo "已取消录制。"
  exit 1
fi

echo "=================================================="
echo " 即将录制 ${DURATION} 秒，请对着摄像头正常说话"
echo " 摄像头: $VIDEO_DEV   麦克风: $AUDIO_DEV"
echo "=================================================="
echo "3秒后开始录制..."
sleep 3

"$FFMPEG" -y \
  -f v4l2 -err_detect ignore_err -input_format mjpeg -video_size 1280x720 -framerate 30 -i "$VIDEO_DEV" \
  -f alsa -i "$AUDIO_DEV" \
  -t "$DURATION" \
  -c:v libx264 -preset veryfast -r 25 \
  -c:a aac -ar 16000 -ac 1 \
  "$OUT_MP4" -loglevel error

echo "录制完成: $OUT_MP4"
echo "开始跑 Light-ASD 推理 (权重: $CHECKPOINT) ..."

cd "$LASD_DIR"
export PATH="$HERE/venv/bin:$HERE/bin:$PATH"
python Columbia_test.py --videoName "$NAME" --videoFolder demo --pretrainModel "$CHECKPOINT"

echo ""
echo "推理完成，生成结果分析报告..."
"$VENV_PY" "$HERE/analyze_result.py" "$NAME"

echo ""
echo "=================================================="
echo " 可视化结果视频(绿框=判定说话/红框=判定未说话):"
echo " $LASD_DIR/demo/${NAME}/pyavi/video_out.avi"
echo "=================================================="
