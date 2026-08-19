#!/usr/bin/env bash
# 安装 gaze_arbiter 全套运行环境(conda 版)。
#
# 覆盖的是"跑起来"需要的两块:
#   1) gaze_arbiter/examples/web_dashboard.py 这条链路——人脸检测(MediaPipe)+
#      说话人识别(Light-ASD, torch)+ 头部姿态(6DRepNet), 原来跑在
#      light_asd_test/venv 里。
#   2) servo_tuning/head-sdk-face/head-server/src/head_grpc_server.py 这条
#      链路——头部舵机 gRPC 服务, 原来跑在 servo_tuning/venv_face_servo 里。
#
# 这两个 venv 实测没有版本冲突(head_sdk 那套 grpcio/protobuf 装进
# light_asd_test/venv 时专门验证过), 所以这里合并成一个 conda 环境, 不用来
# 回切两个 venv。包版本全部锁死成 2026-08-19 当天实测能跑通的组合, 不做
# "装最新版"这种事——这些包(尤其 torch/opencv/mediapipe/numpy)之间的 ABI
# 兼容性是踩过坑验证过的, 换新版本前请先在别的环境测好。
#
# 用法: bash install_conda_env.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_NAME="servo_face"
PYTHON_VERSION="3.12"

if ! command -v conda >/dev/null 2>&1; then
  echo "没找到 conda。先装 Miniconda 再重跑这个脚本:"
  echo "  https://docs.conda.io/en/latest/miniconda.html"
  exit 1
fi

# shellcheck disable=SC1091
source "$(conda info --base)/etc/profile.d/conda.sh"

if conda env list | grep -qE "^${ENV_NAME}[[:space:]]"; then
  echo ">> conda 环境 '${ENV_NAME}' 已存在, 复用并更新依赖(不重建)"
else
  echo ">> 创建 conda 环境 '${ENV_NAME}' (python ${PYTHON_VERSION}) ..."
  conda create -y -n "${ENV_NAME}" "python=${PYTHON_VERSION}"
fi

conda activate "${ENV_NAME}"
echo ">> 当前 python: $(command -v python)  ($(python --version))"

echo ">> 安装 torch/torchvision(CPU 版, 这台机器没有 GPU) ..."
pip install --index-url https://download.pytorch.org/whl/cpu \
  torch==2.13.0+cpu torchvision==0.28.0+cpu

echo ">> 安装其余依赖(版本锁死, 来自 light_asd_test/venv 实测可用的组合) ..."
REQ_FILE="$(mktemp)"
trap 'rm -f "${REQ_FILE}"' EXIT
cat > "${REQ_FILE}" <<'REQS'
absl-py==2.5.0
beautifulsoup4==4.15.0
certifi==2026.7.22
cffi==2.1.0
charset-normalizer==3.4.9
click==8.4.2
contourpy==1.3.3
cycler==0.12.1
filelock==3.29.0
flatbuffers==25.12.19
fonttools==4.63.0
fsspec==2026.4.0
gdown==6.1.0
grpcio==1.65.4
grpcio-tools==1.65.4
idna==3.18
Jinja2==3.1.6
joblib==1.5.3
kiwisolver==1.5.0
MarkupSafe==3.0.3
matplotlib==3.11.1
mediapipe==1.0.0
mpmath==1.3.0
narwhals==2.24.0
networkx==3.6.1
numpy==2.4.4
opencv-contrib-python==5.0.0.93
opencv-python==5.0.0.93
packaging==26.2
pandas==3.0.5
pillow==12.2.0
protobuf==5.29.4
pycparser==3.0
pyparsing==3.3.2
pyserial==3.5
PySocks==1.7.1
python-dateutil==2.9.0.post0
python_speech_features==0.6
PyYAML==6.0.3
requests==2.34.2
scenedetect==0.5.6.1
scikit-learn==1.9.0
scipy==1.18.0
six==1.17.0
sixdrepnet==0.1.6
sounddevice==0.5.5
soupsieve==2.9.1
sympy==1.14.0
threadpoolctl==3.6.0
tqdm==4.70.0
typing_extensions==4.15.0
urllib3==2.7.0
REQS
pip install -r "${REQ_FILE}"

echo ">> 安装 rena2_sdk_api(私有 wheel, 不在 PyPI 上) ..."
pip install "${ROOT}/servo_tuning/head-sdk-face/head-sdk/rena2_sdk_api-0.1.0-py3-none-any.whl"

echo ">> 安装 head-sdk(本地源码, 依赖 rena2_sdk_api, 上一步已装好) ..."
pip install "${ROOT}/servo_tuning/head-sdk-face/head-sdk"

echo ""
echo "=================================================="
echo "环境装好了。手动激活用:  conda activate ${ENV_NAME}"
echo "一键启动用:            bash ${ROOT}/start.sh"
echo "=================================================="
