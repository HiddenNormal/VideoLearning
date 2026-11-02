"""候选镜头集：把结构（shots）+ 画面质量（keyframes）合成「可打标」的干净集合。

两道过滤：

1. **自适应片头片尾检测**
   实测每集开头是 MGM 狮子片头 + 标题卡（20~35 秒），结尾是字幕板。
   这些镜头亮度/对比度不低（狮子是亮的、狮子还在动），单靠质量阈值抓不住。
   做法：从前往后找「连续 N 个镜头都满足 亮度>60 且 运动量>8」的位置作为正片起点；
   从后往前同理找终点。找不到就退回全片范围。

2. **画面质量阈值**（config 里的 MIN_BRIGHTNESS / MIN_CONTRAST / MIN_MOTION）
   滤掉中间的淡入淡出、黑场、静止字幕板。

经过这两道，剩下的才是真正值得送进 VLM 打标的镜头。
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from . import config

# 自适应正片检测参数
#
# 只用亮度、不用运动量：实测正常正片里也有大量「几乎不动」的镜头
#（P2 在 38~70 秒连续几个镜头 motion 只有 1.5~3.6），用运动会把正片起点
# 一路推到 70 秒之后，误伤内容。
CONTENT_MIN_BRIGHTNESS = 75.0
CONTENT_RUN = 3  # 连续多少个镜头满足条件才认定正片
SCAN_WINDOW = 90.0  # 只在首/尾 90 秒内找边界
START_FALLBACK = 30.0  # 找不到时按经验值
MAX_START = 60.0  # 片头不可能超过 60 秒，硬上限防误判
END_FALLBACK = 20.0  # 从结尾往前退多少秒


@dataclass
class Candidate:
    shot_id: str
    p: int
    t0: float
    t1: float
    dur: float
    quality: str
    brightness: float
    contrast: float
    motion: float

    @property
    def bucket(self) -> str:
        """时长分档，供后续打标策略参考。"""
        if self.dur < 2:
            return "short"
        if self.dur <= 6:
            return "ideal"
        return "long"


def is_clean(rec: dict) -> bool:
    return (
        rec["brightness"] >= config.MIN_BRIGHTNESS
        and rec["contrast"] >= config.MIN_CONTRAST
        and rec["motion"] >= config.MIN_MOTION
    )


def _content_ok(rec: dict) -> bool:
    return rec["brightness"] >= CONTENT_MIN_BRIGHTNESS


def content_bounds(recs: list[dict]) -> tuple[float, float]:
    """返回（正片起点, 正片终点）。

    判据只用亮度：片头（MGM 狮子 + 标题卡）整体偏暗，正片整体偏亮。
    在首/尾 90 秒窗口内找「连续 3 个镜头亮度 ≥ 75」的位置作为边界，
    再分别用 60 秒 / 90 秒的硬上限防止误判把内容切掉。
    """
    recs = sorted(recs, key=lambda r: r["t0"])
    if not recs:
        return 0.0, 0.0

    duration = recs[-1]["t1"]

    # ---- 起点 ----
    head = [r for r in recs if r["t0"] < SCAN_WINDOW]
    start = START_FALLBACK
    for i in range(len(head) - CONTENT_RUN + 1):
        window = head[i : i + CONTENT_RUN]
        if all(_content_ok(r) for r in window):
            start = window[0]["t0"]
            break
    start = min(start, MAX_START)

    # ---- 终点 ----
    tail = [r for r in recs if r["t1"] > duration - SCAN_WINDOW]
    end = duration - END_FALLBACK
    for i in range(len(tail) - CONTENT_RUN, -1, -1):
        window = tail[i : i + CONTENT_RUN]
        if all(_content_ok(r) for r in window):
            end = window[-1]["t1"]
            break
    end = max(end, duration - SCAN_WINDOW)

    if end <= start + 30:  # 边界不可信，退回全片
        return 0.0, duration
    return round(start, 3), round(end, 3)


def build(
    shots,
    keyframes_path: Path | None = None,
) -> tuple[list[Candidate], dict]:
    """返回（候选镜头列表, 统计信息）。"""
    keyframes_path = keyframes_path or config.KEYFRAMES_JSONL
    if not keyframes_path.exists():
        raise SystemExit(
            f"找不到 {keyframes_path}（先跑 tjgif keyframes）"
        )

    quality: dict[str, dict] = {}
    by_ep: dict[int, list[dict]] = {}
    with keyframes_path.open(encoding="utf-8") as f:
        for line in f:
            rec = json.loads(line)
            quality[rec["shot_id"]] = rec
            by_ep.setdefault(int(rec["p"]), []).append(rec)

    bounds = {p: content_bounds(recs) for p, recs in by_ep.items()}

    stats = {
        "total": 0,
        "drop_no_metrics": 0,
        "drop_outside_content": 0,
        "drop_bad_quality": 0,
        "kept": 0,
    }
    cands: list[Candidate] = []

    for shot in shots:
        stats["total"] += 1
        rec = quality.get(shot.shot_id)
        if rec is None:
            stats["drop_no_metrics"] += 1
            continue
        lo, hi = bounds.get(shot.p, (0.0, 0.0))
        if shot.t0 < lo or shot.t1 > hi:
            stats["drop_outside_content"] += 1
            continue
        if not is_clean(rec):
            stats["drop_bad_quality"] += 1
            continue
        cands.append(
            Candidate(
                shot_id=shot.shot_id,
                p=shot.p,
                t0=shot.t0,
                t1=shot.t1,
                dur=shot.dur,
                quality=shot.quality,
                brightness=rec["brightness"],
                contrast=rec["contrast"],
                motion=rec["motion"],
            )
        )
        stats["kept"] += 1

    stats["bounds"] = {p: bounds[p] for p in sorted(bounds)}
    return cands, stats


def write(cands: list[Candidate], out: Path | None = None) -> Path:
    config.ensure_data_dir()
    out = out or config.CANDIDATES_JSONL
    with out.open("w", encoding="utf-8") as f:
        for c in cands:
            f.write(
                json.dumps(asdict(c) | {"bucket": c.bucket}, ensure_ascii=False) + "\n"
            )
    return out


def summarize(cands: list[Candidate], stats: dict) -> str:
    total = stats["total"] or 1
    durs = sorted(c.dur for c in cands)
    buckets: dict[str, int] = {}
    for c in cands:
        buckets[c.bucket] = buckets.get(c.bucket, 0) + 1

    lines = [
        f"镜头总数        : {stats['total']:,}",
        f"  无质量数据      : {stats['drop_no_metrics']:,}",
        f"  片头片尾排除    : {stats['drop_outside_content']:,}",
        f"  画质不合格      : {stats['drop_bad_quality']:,}",
        f"  最终候选        : {stats['kept']:,}（{100*stats['kept']/total:.1f}%）",
    ]
    if durs:
        lines += [
            f"候选总时长      : {sum(durs)/3600:.2f} 小时",
            f"时长分布        : ideal(2~6s) {buckets.get('ideal',0):,}  "
            f"short(<2s) {buckets.get('short',0):,}  long(>6s) {buckets.get('long',0):,}",
            f"每集候选数      : 约 {stats['kept']//max(1,len(stats['bounds']))} 个",
        ]
    # 每集候选数分布，确认没有整集被误杀
    per_ep: dict[int, int] = {}
    for c in cands:
        per_ep[c.p] = per_ep.get(c.p, 0) + 1
    if per_ep:
        counts = sorted(per_ep.values())
        low = [p for p, n in per_ep.items() if n < 10]
        lines.append(
            f"单集候选数      : 最少 {counts[0]} / 中位 {counts[len(counts)//2]} / 最多 {counts[-1]}"
            + (f"；⚠ 少于 10 个的集 {len(low)} 个" if low else "；无整集误杀")
        )

    # 片头长度分布，验证 30 秒假设
    starts = [v[0] for v in stats["bounds"].values()]
    if starts:
        s = sorted(starts)
        lines.append(
            f"正片起点        : 中位 {s[len(s)//2]:.1f}s  "
            f"p10 {s[int(0.1*len(s))]:.1f}s  p90 {s[int(0.9*len(s))]:.1f}s"
        )
    return "\n".join(lines)
