#!/usr/bin/env python3
"""用 PySceneDetect 检测全库镜头切点。

为什么不用 ffmpeg 的 scene 滤镜：实测在动画上会严重漏检（旧片源上
P54 用 ffmpeg 只检出 19 个切点，PySceneDetect 检出 56 个），
因为 ffmpeg 只比帧差，动画的连续运动会被判成"没变化"。

实测速度：约 5~7 秒/集（含解码），240 集约 20~30 分钟。

用法：
    python scripts/detect_shots.py --compare      # 三种检测器/阈值对比
    python scripts/detect_shots.py --all          # 全库检测，写 shots-raw.jsonl
    python scripts/detect_shots.py --all --limit 5
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from scenedetect import AdaptiveDetector, ContentDetector, detect  # noqa: E402

from tjgif import config  # noqa: E402

NUM_RE = re.compile(r"^(\d{1,4})")


def make_detector(kind: str, threshold: float, min_scene_len: int = 15):
    if kind == "adaptive":
        return AdaptiveDetector()
    return ContentDetector(threshold=threshold, min_scene_len=min_scene_len)


def episode_index(path: Path) -> int:
    m = NUM_RE.match(path.name)
    return int(m.group(1)) if m else -1


def all_episodes(limit: int = 0) -> list[Path]:
    eps = sorted(config.VIDEO_DIR.glob("*.mp4"), key=episode_index)
    return eps[:limit] if limit else eps


def detect_one(path: Path, kind: str, threshold: float) -> dict:
    t0 = time.time()
    scenes = detect(str(path), make_detector(kind, threshold))
    elapsed = time.time() - t0
    shots = [{"t0": round(s.seconds, 3), "t1": round(e.seconds, 3)} for s, e in scenes]
    duration = shots[-1]["t1"] if shots else 0.0
    return {
        "p": episode_index(path),
        "file": path.name,
        "duration_s": round(duration, 2),
        "n_shots": len(shots),
        "elapsed_s": round(elapsed, 2),
        "shots": shots,
    }


def shot_stats(lengths: list[float]) -> dict:
    if not lengths:
        return {}
    s = sorted(lengths)
    n = len(s)

    def pct(q: float) -> float:
        return s[min(n - 1, int(q * n))]

    return {
        "n": n,
        "mean": round(statistics.fmean(s), 2),
        "median": round(statistics.median(s), 2),
        "p10": round(pct(0.10), 2),
        "p90": round(pct(0.90), 2),
        "max": round(s[-1], 2),
        "pct_lt_1s": round(100 * sum(1 for x in s if x < 1.0) / n, 1),
        "pct_lt_2s": round(100 * sum(1 for x in s if x < 2.0) / n, 1),
        "pct_gt_8s": round(100 * sum(1 for x in s if x > 8.0) / n, 1),
    }


def compare() -> None:
    """在前 4 集上对比检测器，确定生产参数。"""
    eps = all_episodes()[:4]
    configs = [
        ("content t=27", "content", 27.0),
        ("content t=32", "content", 32.0),
        ("adaptive   ", "adaptive", 0.0),
    ]
    print(f"对比 {len(eps)} 集 × {len(configs)} 种配置\n")
    header = f"  {'配置':<14}" + "".join(f"{'#' + str(episode_index(p)):>9}" for p in eps)
    print(header + f"{'合计镜头':>9}{'平均':>8}")
    for label, kind, th in configs:
        counts, all_len, t0 = [], [], time.time()
        for p in eps:
            res = detect_one(p, kind, th)
            counts.append(res["n_shots"])
            all_len += [s["t1"] - s["t0"] for s in res["shots"]]
        line = f"  {label:<14}" + "".join(f"{c:>9}" for c in counts)
        line += f"{sum(counts):>9}{(sum(all_len) / len(all_len)):>7.2f}s"
        print(line + f"   ({time.time() - t0:.0f}s)")


def run_all(kind: str, threshold: float, limit: int, out: Path) -> None:
    eps = all_episodes(limit)
    out.parent.mkdir(parents=True, exist_ok=True)
    results: list[dict] = []
    all_lengths: list[float] = []
    t_start = time.time()

    print(f"检测 {len(eps)} 集（{kind} t={threshold}）→ {out}\n")
    print(f"  {'集':>5} {'镜头数':>6} {'平均':>7} {'中位':>7} {'耗时':>6}")
    with out.open("w", encoding="utf-8") as f:
        for i, path in enumerate(eps, 1):
            res = detect_one(path, kind, threshold)
            results.append(res)
            lengths = [s["t1"] - s["t0"] for s in res["shots"]]
            all_lengths += lengths
            f.write(json.dumps(res, ensure_ascii=False) + "\n")

            if i <= 10 or i % 20 == 0 or i == len(eps):
                mean = statistics.fmean(lengths) if lengths else 0
                med = statistics.median(lengths) if lengths else 0
                print(
                    f"  #{res['p']:<4} {res['n_shots']:>6} {mean:>6.2f}s "
                    f"{med:>6.2f}s {res['elapsed_s']:>5.1f}s"
                )

    total_s = time.time() - t_start
    agg = shot_stats(all_lengths)
    total_shots = sum(r["n_shots"] for r in results)
    total_dur = sum(r["duration_s"] for r in results)

    print(f"\n=== 汇总（{len(results)} 集，总耗时 {total_s / 60:.1f} 分钟）===")
    print(f"  总时长      : {total_dur / 3600:.2f} 小时")
    print(f"  总镜头数    : {total_shots:,}")
    print(f"  平均镜头    : {agg['mean']} 秒")
    print(f"  中位镜头    : {agg['median']} 秒")
    print(f"  p10 / p90   : {agg['p10']}s / {agg['p90']}s")
    print(f"  <1s 占比    : {agg['pct_lt_1s']}%")
    print(f"  <2s 占比    : {agg['pct_lt_2s']}%")
    print(f"  >8s 占比    : {agg['pct_gt_8s']}%")
    print(f"  关键帧(4/镜头) ~ {total_shots * 4:,} 张")

    med_shots = statistics.median(r["n_shots"] for r in results)
    odd = [r for r in results if r["n_shots"] < med_shots * 0.4]
    if odd:
        print("\n  ⚠ 镜头数偏少的集（可能检测失败或本身是长镜头集）：")
        for r in odd:
            print(f"     #{r['p']} {r['n_shots']} 个镜头 / {r['duration_s']:.0f}s")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--compare", action="store_true")
    ap.add_argument("--detector", default="content", choices=["content", "adaptive"])
    ap.add_argument("--threshold", type=float, default=27.0)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()

    if args.compare:
        compare()
        return
    if not args.all:
        ap.print_help()
        return
    out = args.out or config.SHOTS_RAW_JSONL
    run_all(args.detector, args.threshold, args.limit, out)


if __name__ == "__main__":
    main()
