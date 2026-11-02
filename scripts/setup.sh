#!/usr/bin/env bash
# =============================================================================
#  VideoLearning 开发环境初始化（WSL / Ubuntu，全程不需要 sudo）
#
#  用法：
#      bash scripts/setup.sh
#      TJGIF_VENV=$HOME/tj-gif/.venv bash scripts/setup.sh   # 复用已有环境
#
#  做的事：
#      1. 装 uv（单文件包管理器，绕过 Ubuntu 没装 python3-venv 的问题）
#      2. 在 ext4 上建 venv（不要放 /mnt/e，import 会慢一个数量级）
#      3. 装依赖 + 以可编辑模式装项目本身
#      4. 自检
# =============================================================================
set -euo pipefail

VENV="${TJGIF_VENV:-$HOME/video-learning-venv}"
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "== 仓库路径: $REPO"
echo "== 虚拟环境: $VENV"

echo
echo "== 1. 安装 uv =="
if [ -x "$HOME/.local/bin/uv" ]; then
  echo "  已安装"
else
  curl -LsSf https://astral.sh/uv/install.sh | sh
fi
UV="$HOME/.local/bin/uv"
"$UV" --version

echo
echo "== 2. 创建虚拟环境 =="
if [ ! -x "$VENV/bin/python" ]; then
  "$UV" venv "$VENV" --python 3.12
else
  echo "  已存在"
fi
"$VENV/bin/python" -V

echo
echo "== 3. 安装依赖（约 120MB）=="
"$UV" pip install --python "$VENV/bin/python" \
  numpy opencv-python-headless scenedetect pillow tqdm pandas pyyaml

echo
echo "== 4. 安装项目本身（提供 tjgif 命令）=="
if "$UV" pip install --python "$VENV/bin/python" -e "$REPO"; then
  echo "  已安装"
else
  echo "  可编辑安装失败，改用 python -m tjgif（需在仓库根目录）"
fi

echo
echo "== 5. 自检 =="
PYTHONPATH="$REPO/src" "$VENV/bin/python" -m tjgif info

echo
echo "== 完成 =="
echo "  激活： source $VENV/bin/activate"
echo "  下一步： bash scripts/fetch_tj.sh --list"
