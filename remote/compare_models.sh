#!/usr/bin/env bash
# =============================================================================
#  用同一批镜头对比两个模型的打标质量
#
#  用法：
#      bash compare_models.sh                    # 默认 7B vs 32B，比 20 条
#      bash compare_models.sh ./ 30              # 比 30 条
#      A_MODEL=... B_MODEL=... bash compare_models.sh
#
#  为什么值得做：7B 全量只要 20~35 分钟，32B 要 1~2 小时。
#  但如果 7B 的摘要笼统到不能用于检索，省下的时间毫无意义。
#  先花 10 分钟看 20 条，再决定全量用哪个模型。
# =============================================================================
set -euo pipefail

INPUT="${1:-./}"
LIMIT="${2:-20}"
A_MODEL="${A_MODEL:-Qwen/Qwen2.5-VL-7B-Instruct}"
B_MODEL="${B_MODEL:-Qwen/Qwen3-VL-32B-Instruct}"
OUT="./compare"

PY="${PYTHON:-python}"

echo "=== 对比配置 ==="
echo "  输入    : $INPUT"
echo "  条数    : $LIMIT"
echo "  模型 A  : $A_MODEL"
echo "  模型 B  : $B_MODEL"
echo

rm -rf "$OUT"

echo "--- 跑 A: $A_MODEL ---"
"$PY" label_remote.py --input-dir "$INPUT" --out "$OUT/a" \
  --model "$A_MODEL" --batch-size 8 --limit "$LIMIT"

echo
echo "--- 跑 B: $B_MODEL ---"
"$PY" label_remote.py --input-dir "$INPUT" --out "$OUT/b" \
  --model "$B_MODEL" --batch-size 2 --limit "$LIMIT"

echo
echo "=== 并排对比 ==="
"$PY" - "$OUT" "$A_MODEL" "$B_MODEL" <<'PY'
import json
import sys
from pathlib import Path

out = Path(sys.argv[1])
a_model, b_model = sys.argv[2], sys.argv[3]


def load(path):
    records = {}
    if not path.exists():
        return records
    with path.open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                rec = json.loads(line)
                records[rec["shot_id"]] = rec
    return records


a = load(out / "a" / "labels.jsonl")
b = load(out / "b" / "labels.jsonl")
common = sorted(set(a) & set(b))


def clip(text, n):
    text = (text or "").replace("\n", " ")
    return text if len(text) <= n else text[: n - 1] + "…"


print(f"两边都成功: {len(common)} 条    A 独有: {len(set(a)-set(b))}   B 独有: {len(set(b)-set(a))}\n")

for i, sid in enumerate(common, 1):
    ra, rb = a[sid], b[sid]
    print(f"[{i}] {sid}  {ra['t0']:.1f}-{ra['t1']:.1f}s")
    print(f"  A 摘要 : {clip(ra['labels']['summary_zh'], 70)}")
    print(f"  B 摘要 : {clip(rb['labels']['summary_zh'], 70)}")
    print(f"  A 角色 : {ra['labels']['characters']}   道具: {ra['labels']['objects']}")
    print(f"  B 角色 : {rb['labels']['characters']}   道具: {rb['labels']['objects']}")
    print()

# 统计对比
def stats(recs):
    if not recs:
        return "无数据"
    lens = [len(r["labels"]["summary_zh"]) for r in recs.values()]
    chars = sum(1 for r in recs.values() if r["labels"]["characters"]) / len(recs)
    objs = sum(1 for r in recs.values() if r["labels"]["objects"]) / len(recs)
    return (f"平均摘要 {sum(lens)/len(lens):.0f} 字   "
            f"有角色 {chars*100:.0f}%   有道具 {objs*100:.0f}%")

print("=" * 60)
print(f"A ({a_model})")
print(f"  {stats(a)}")
print(f"B ({b_model})")
print(f"  {stats(b)}")
print("\n判断标准：摘要是否说清「谁对谁做了什么、结果如何」；角色有没有认错；道具对不对。")
print("如果 A 已经够用，用 A 全量（快 3~4 倍）；不够就上 B。")
PY
