#!/usr/bin/env bash
# =============================================================================
#  在远程 GPU 机器（H100 等）上准备打标环境
#
#  用法：
#      bash setup_h100.sh                 # 基础环境（torch + transformers）
#      bash setup_h100.sh --with-vllm     # 额外装 vLLM（吞吐高很多）
#      bash setup_h100.sh --hf-mirror     # 走国内 HF 镜像（机器在境内时用）
#
#  特点：
#      * 全程不需要 sudo（用 uv 管理 Python 环境）
#      * 不污染系统 Python
#      * 装完自动自检：GPU 型号、显存、bf16 支持
# =============================================================================
set -euo pipefail

WITH_VLLM=0
HF_MIRROR=0
for arg in "$@"; do
  case "$arg" in
    --with-vllm) WITH_VLLM=1 ;;
    --hf-mirror) HF_MIRROR=1 ;;
    -h|--help) sed -n '2,14p' "$0"; exit 0 ;;
    *) echo "未知参数: $arg"; exit 1 ;;
  esac
done

VENV="${TJGIF_VENV:-$HOME/tjgif-venv}"

echo "== 1. 检查 GPU =="
if ! command -v nvidia-smi >/dev/null 2>&1; then
  echo "  [错误] 没有 nvidia-smi，这机器看不到 GPU（驱动没装？）"
  exit 1
fi
nvidia-smi --query-gpu=index,name,memory.total,driver_version \
  --format=csv,noheader | sed 's/^/  /'
GPU_COUNT=$(nvidia-smi --query-gpu=index --format=csv,noheader | wc -l)
echo "  GPU 数量: $GPU_COUNT"

echo
echo "== 2. 安装 uv =="
if [ -x "$HOME/.local/bin/uv" ]; then
  echo "  已安装"
else
  curl -LsSf https://astral.sh/uv/install.sh | sh
fi
UV="$HOME/.local/bin/uv"
"$UV" --version

echo
echo "== 3. 创建虚拟环境 =="
if [ ! -x "$VENV/bin/python" ]; then
  "$UV" venv "$VENV" --python 3.12 || "$UV" venv "$VENV"
else
  echo "  已存在: $VENV"
fi
"$VENV/bin/python" -V

echo
echo "== 4. 安装依赖 =="
# torch 在 Linux 上从 PyPI 装的就是 CUDA 12.x 版本
"$UV" pip install --python "$VENV/bin/python" \
  torch torchvision transformers accelerate pillow tqdm

if [ "$WITH_VLLM" -eq 1 ]; then
  echo
  echo "== 5. 安装 vLLM（较大，约 2GB）=="
  "$UV" pip install --python "$VENV/bin/python" vllm || {
    echo "  [警告] vLLM 安装失败，可以继续用 --backend transformers"
  }
fi

if [ "$HF_MIRROR" -eq 1 ]; then
  echo
  echo "== 6. 配置 HF 镜像 =="
  echo '  export HF_ENDPOINT=https://hf-mirror.com' >> "$HOME/.bashrc"
  echo "  已写入 ~/.bashrc：HF_ENDPOINT=https://hf-mirror.com"
  echo "  当前会话请手动执行： export HF_ENDPOINT=https://hf-mirror.com"
fi

echo
echo "== 7. 自检 =="
"$VENV/bin/python" - <<'PY'
import torch
print(f"  torch          {torch.__version__}")
print(f"  CUDA 可用      {torch.cuda.is_available()}")
if torch.cuda.is_available():
    n = torch.cuda.device_count()
    print(f"  GPU 数量       {n}")
    for i in range(n):
        p = torch.cuda.get_device_properties(i)
        print(f"    [{i}] {p.name}  {p.total_memory/2**30:.1f} GB")
    print(f"  bf16 支持      {torch.cuda.is_bf16_supported()}")
import transformers
print(f"  transformers   {transformers.__version__}")
PY

echo
echo "== 完成 =="
echo "  虚拟环境: $VENV"
echo "  激活：    source $VENV/bin/activate"
echo "  冒烟：    python label_remote.py --input-dir ./input --out ./out --backend mock --limit 50"
