#!/usr/bin/env python3
"""检查画面质量度量：看片头/标题卡能不能被自动识别出来。

用法：
    python inspect_quality.py --p 1          # 看 P1 全部镜头的指标
    python inspect_quality.py --p 1 --head 15
    python inspect_quality.py --p 3 --flags  # 只看会被过滤掉的
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tjgif import config  # noqa: E402


def load() -> list[dict]:
    if not config.KEYFRAMES_JSONL.exists():
        sys.exit("还没有 keyframes.jsonl，先跑 tjgif keyframes")
    return [
        json.loads(line)
        for line in config.KEYFRAMES_JSONL.open(encoding="utf-8")
    ]


def flagged(rec: dict) -> str:
    reasons = []
    if rec["brightness"] < config.MIN_BRIGHTNESS:
        reasons.append("暗")
    if rec["contrast"] < config.MIN_CONTRAST:
        reasons.append("平")
    if rec["motion"] < config.MIN_MOTION:
        reasons.append("静")
    return "+".join(reasons)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--p", type=int, default=0, help="只看某一集")
    ap.add_argument("--head", type=int, default=0, help="只看前 N 个镜头")
    ap.add_argument("--flags", action="store_true", help="只看被判定为垃圾的")
    args = ap.parse_args()

    recs = load()
    if args.p:
        recs = [r for r in recs if r["p"] == args.p]
    if args.head:
        recs = recs[: args.head]

    print(f"{'镜头':<14} {'起始':>7} {'时长':>6} {'亮度':>7} {'对比':>6} {'运动':>6}  判定")
    shown = 0
    for r in recs:
        f = flagged(r)
        if args.flags and not f:
            continue
        print(
            f"{r['shot_id']:<14} {r['t0']:>6.1f}s {r['dur']:>5.2f}s "
            f"{r['brightness']:>7.1f} {r['contrast']:>6.1f} {r['motion']:>6.2f}  "
            f"{'✗ ' + f if f else '✓'}"
        )
        shown += 1

    total = len(load())
    allrecs = load()
    bad = sum(1 for r in allrecs if flagged(r))
    print(f"\n共 {total:,} 个镜头，判定为垃圾 {bad:,}（{100*bad/total:.1f}%）")


if __name__ == "__main__":
    main()
