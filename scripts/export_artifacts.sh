#!/usr/bin/env bash
# =============================================================================
#  把「值得进仓库」的元数据从 data/ 导出到 data/derived/
#
#  仓库里放：剧集目录、镜头切点、画面质量度量、候选集、标签
#  不放：片源、关键帧图、故事板（体积大，可由片源重建）
# =============================================================================
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DATA="${TJGIF_DATA:-$REPO/data}"
DST="$REPO/data/derived"

mkdir -p "$DST"

echo "== 导出结构化数据 =="
for f in episodes.jsonl shots.jsonl candidates.jsonl keyframes.jsonl labels.jsonl; do
  src="$DATA/$f"
  if [ -f "$src" ]; then
    cp "$src" "$DST/$f"
    printf "  %-20s %6.2f MB\n" "$f" "$(du -m "$src" | cut -f1)"
  else
    printf "  %-20s (还没有，跳过)\n" "$f"
  fi
done

echo
echo "== data/derived 现状 =="
du -sh "$DST" 2>/dev/null | sed 's/^/  总大小: /'
ls -1 "$DST" 2>/dev/null | sed 's/^/  /'
