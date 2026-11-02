"""镜头后处理：合并碎片、切分长镜头。

PySceneDetect 的原始输出是「切点边界」，但检索需要的单元是
**2~6 秒、语义完整的一小段动作**。所以要做两步：

1. 合并 <0.8s 的碎片（闪白、快速运镜造成的误切）
2. 切分 >8s 的长镜头（一个长镜头里常有多个笑点，整段描述会糊）

长镜头的切分点目前用**均分**兜底；v1.1 会改成按帧差能量找动作峰值。

注：弹幕热度先验已按需求暂时移除，本模块只依赖画面结构。
"""
from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path

from . import config

MIN_SHOT = 0.8  # 短于此值视为误切，合并
MAX_SHOT = 8.0  # 长于此值需要切分
TARGET_MAX = 6.0  # 切分后的目标上限


@dataclass
class Shot:
    shot_id: str
    p: int
    idx: int
    t0: float
    t1: float
    quality: str = "?"
    split: str = ""  # "even" 表示该段由长镜头均分而来

    @property
    def dur(self) -> float:
        return round(self.t1 - self.t0, 3)


def merge_short(
    segs: list[tuple[float, float]], min_len: float = MIN_SHOT
) -> list[tuple[float, float]]:
    """把过短的片段并入相邻更长的一段。"""
    work = [[a, b] for a, b in segs]
    changed = True
    while changed and len(work) > 1:
        changed = False
        for i in range(len(work)):
            a, b = work[i]
            if b - a >= min_len:
                continue
            prev_len = (work[i - 1][1] - work[i - 1][0]) if i > 0 else -1.0
            next_len = (work[i + 1][1] - work[i + 1][0]) if i < len(work) - 1 else -1.0
            if i > 0 and prev_len >= next_len:
                work[i - 1][1] = b
                del work[i]
            elif i < len(work) - 1:
                work[i + 1][0] = a
                del work[i]
            else:
                work[i - 1][1] = b
                del work[i]
            changed = True
            break
    return [(a, b) for a, b in work]


def split_long(
    segs: list[tuple[float, float]],
    max_len: float = MAX_SHOT,
    target: float = TARGET_MAX,
) -> list[tuple[float, float, str]]:
    """把过长的片段均分成若干段（v1 兜底策略）。"""
    out: list[tuple[float, float, str]] = []
    for a, b in segs:
        d = b - a
        if d <= max_len:
            out.append((a, b, ""))
            continue
        n = max(2, math.ceil(d / target))
        step = d / n
        for k in range(n):
            out.append((a + k * step, a + (k + 1) * step, "even"))
    return out


def build(episodes, raw_path: Path | None = None) -> list[Shot]:
    raw_path = raw_path or config.SHOTS_RAW_JSONL
    if not raw_path.exists():
        raise SystemExit(
            f"找不到原始切点文件：{raw_path}（先跑 scripts/detect_shots.py --all）"
        )

    meta = {e.p: e for e in episodes}
    shots: list[Shot] = []

    with raw_path.open(encoding="utf-8") as f:
        for line in f:
            rec = json.loads(line)
            p = int(rec["p"])
            segs = merge_short([(float(s["t0"]), float(s["t1"])) for s in rec["shots"]])
            segs = split_long(segs)
            ep = meta.get(p)
            for i, (a, b, flag) in enumerate(segs):
                shots.append(
                    Shot(
                        shot_id=f"P{p:03d}_{i:04d}",
                        p=p,
                        idx=i,
                        t0=round(a, 3),
                        t1=round(b, 3),
                        quality=ep.quality if ep else "?",
                        split=flag,
                    )
                )
    return shots


def write(shots: list[Shot], out: Path | None = None) -> Path:
    config.ensure_data_dir()
    out = out or config.SHOTS_JSONL
    with out.open("w", encoding="utf-8") as f:
        for s in shots:
            f.write(json.dumps(asdict(s) | {"dur": s.dur}, ensure_ascii=False) + "\n")
    return out


def summarize(shots: list[Shot]) -> str:
    if not shots:
        return "没有镜头"
    durs = sorted(s.dur for s in shots)
    n = len(durs)
    mean = sum(durs) / n
    median = durs[n // 2]
    buckets = {"<1s": 0, "1-2s": 0, "2-4s": 0, "4-8s": 0, ">8s": 0}
    for d in durs:
        if d < 1:
            buckets["<1s"] += 1
        elif d < 2:
            buckets["1-2s"] += 1
        elif d < 4:
            buckets["2-4s"] += 1
        elif d < 8:
            buckets["4-8s"] += 1
        else:
            buckets[">8s"] += 1
    even = sum(1 for s in shots if s.split == "even")
    per_ep: dict[int, int] = {}
    for s in shots:
        per_ep[s.p] = per_ep.get(s.p, 0) + 1
    counts = sorted(per_ep.values())
    return "\n".join(
        [
            f"镜头总数      : {n:,}",
            f"平均 / 中位   : {mean:.2f}s / {median:.2f}s",
            f"时长分布      : " + "  ".join(f"{k} {v}" for k, v in buckets.items()),
            f"长镜头均分段  : {even:,}（{100 * even / n:.1f}%）",
            f"单集镜头数    : 最少 {counts[0]} / 中位 {counts[len(counts)//2]} / 最多 {counts[-1]}",
            f"总时长覆盖    : {sum(durs) / 3600:.2f} 小时",
        ]
    )
