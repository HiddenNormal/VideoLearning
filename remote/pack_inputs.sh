#!/usr/bin/env bash
# =============================================================================
#  打包打标输入，传到远程 GPU 机器
#
#  用法：
#      bash remote/pack_inputs.sh                 # 默认 ~/videolearning-label-input.tar.gz
#      bash remote/pack_inputs.sh /tmp/x.tar.gz
#
#  前置：本地已跑完 keyframes + candidates
#
#  内容：
#      candidates.jsonl          候选镜头清单
#      episodes.jsonl            剧集元数据
#      storyboards/*.jpg         只打包候选镜头用到的（约 1GB）
#      label_remote.py 等        打标脚本
# =============================================================================
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DATA="${TJGIF_DATA:-$REPO/data}"
TARBALL="${1:-$HOME/videolearning-label-input.tar.gz}"
STAGE="$HOME/.vl-pack"

if [ ! -f "$DATA/candidates.jsonl" ]; then
  echo "[错误] 找不到 $DATA/candidates.jsonl"
  echo "       先在本机跑完： python -m tjgif candidates"
  exit 1
fi
if [ ! -d "$DATA/storyboards" ]; then
  echo "[错误] 找不到 $DATA/storyboards"
  echo "       先在本机跑完： python -m tjgif keyframes"
  exit 1
fi

echo "== 清理旧暂存目录 =="
rm -rf "$STAGE"
mkdir -p "$STAGE/storyboards"

echo "== 拷贝清单与代码 =="
cp "$DATA/candidates.jsonl" "$STAGE/"
cp "$DATA/episodes.jsonl" "$STAGE/" 2>/dev/null || true
for f in label_remote.py schema.py setup_h100.sh compare_models.sh merge_shards.sh README.md; do
  cp "$REPO/remote/$f" "$STAGE/"
done

echo "== 只拷贝候选镜头用到的故事板 =="
python3 - "$DATA" "$STAGE" <<'PY'
import json
import shutil
import sys
from pathlib import Path

data, stage = Path(sys.argv[1]), Path(sys.argv[2])
ids = []
with (data / "candidates.jsonl").open(encoding="utf-8") as f:
    for line in f:
        if line.strip():
            ids.append(json.loads(line)["shot_id"])

src = data / "storyboards"
dst = stage / "storyboards"
copied = missing = 0
for sid in ids:
    s = src / f"{sid}.jpg"
    if s.exists():
        shutil.copy2(s, dst / s.name)
        copied += 1
    else:
        missing += 1
print(f"  拷贝 {copied:,} 张，缺 {missing:,} 张")
PY

echo "== 压缩 =="
rm -f "$TARBALL"
tar czf "$TARBALL" -C "$STAGE" .
SIZE=$(du -h "$TARBALL" | cut -f1)
echo "  完成: $TARBALL  ($SIZE)"

echo
echo "== 传到远程 GPU 机器 =="
echo "  rsync -avP $TARBALL user@host:~/"
echo "  ssh user@host 'mkdir -p ~/tjgif && tar xzf ~/$(basename "$TARBALL") -C ~/tjgif'"
echo
echo "  然后在远程机器上："
echo "    cd ~/tjgif && bash setup_h100.sh && bash compare_models.sh ./ 20"

rm -rf "$STAGE"
