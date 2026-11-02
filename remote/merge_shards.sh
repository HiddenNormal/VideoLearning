#!/usr/bin/env bash
# 合并多卡分片的打标结果
#
#   bash merge_shards.sh <输出根目录> [最终文件名]
#
# 例：out/shard0/labels.jsonl out/shard1/labels.jsonl ...
#     bash merge_shards.sh ./out
set -euo pipefail

ROOT="${1:?用法: bash merge_shards.sh <输出根目录>}"
FINAL_NAME="${2:-labels.jsonl}"

FINAL="$ROOT/$FINAL_NAME"
TMP="$ROOT/.labels.merged.tmp"

shopt -s nullglob
SHARDS=("$ROOT"/shard*/labels.jsonl)
if [ ${#SHARDS[@]} -eq 0 ]; then
  echo "[错误] 没找到 $ROOT/shard*/labels.jsonl"
  exit 1
fi

echo "== 找到 ${#SHARDS[@]} 个分片 =="
for s in "${SHARDS[@]}"; do
  echo "  $(wc -l < "$s") 行  $s"
done

cat "${SHARDS[@]}" | sort -t'"' -k4,4 > "$TMP"

# 去重（同一 shot_id 可能出现在多个分片里，理论上不会，保险起见）
if command -v python3 >/dev/null 2>&1; then
  python3 - "$TMP" "$FINAL" <<'PY'
import json, sys
seen, out = set(), 0
with open(sys.argv[1], encoding="utf-8") as f, open(sys.argv[2], "w", encoding="utf-8") as g:
    for line in f:
        if not line.strip():
            continue
        sid = json.loads(line)["shot_id"]
        if sid in seen:
            continue
        seen.add(sid)
        g.write(line)
        out += 1
print(f"  合并去重后 {out:,} 条")
PY
  rm -f "$TMP"
else
  mv "$TMP" "$FINAL"
fi

echo "  → $FINAL"
