"""关键帧抽取 + 故事板拼图 + 画面质量度量。

每个镜头抽 4 帧（镜头内 10%/35%/65%/90% 位置）：
- `frames/`      单帧 384 宽，给 CLIP 编码用
- `thumbs/`      260 宽缩略图，给 Web UI 的候选网格用
- `storyboards/` 2×2 拼图，给 VLM 打标用 —— 单帧看不出「追」「摔」这类动作，
                 四帧连起来才看得出发生了什么

同时计算三个画面指标，用于过滤垃圾镜头（实测发现每集开头都是
MGM 狮子片头 + 标题卡，全黑或近乎静止，对检索毫无价值）：
- brightness 平均亮度（0~255）：太低 → 暗场/淡入淡出
- contrast   亮度标准差：太低 → 纯色画面/黑屏
- motion     相邻帧平均绝对差：太低 → 静止画面（标题卡、字幕板）

设计取舍：用「每镜头 4 次 ffmpeg 调用 + 线程池」而不是「每集一次抽帧任务」，
虽然进程开销大，但 seek 精确、可断点续跑（已存在的跳过），实现也简单。
全库约 6.3 万帧，12 并发实测约 25 分钟。
"""
from __future__ import annotations

import json
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import numpy as np
from PIL import Image

from . import config

THUMB_WIDTH = 260


def _grab(video: Path, t: float, out: Path, width: int) -> bool:
    """精确抽一帧。ffmpeg 的 -ss 放在 -i 前面是「先快进再精确解码」，又快又准。"""
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-v", "error", "-nostdin",
        "-ss", f"{t:.3f}",
        "-i", str(video),
        "-frames:v", "1",
        "-vf", f"scale={width}:-2:flags=lanczos",
        "-q:v", "4", "-y", str(out),
    ]
    try:
        subprocess.run(cmd, capture_output=True, timeout=60, check=False)
    except subprocess.TimeoutExpired:
        return False
    return out.exists() and out.stat().st_size > 0


def _storyboard(frame_paths: list[Path], out: Path) -> bool:
    imgs: list[Image.Image] = []
    for p in frame_paths:
        if not p.exists():
            continue
        with Image.open(p) as im:
            imgs.append(im.convert("RGB"))
    if not imgs:
        return False

    tile_w = config.TILE_WIDTH
    tiles = []
    for im in imgs[:4]:
        h = max(1, round(im.height * tile_w / im.width))
        tiles.append(im.resize((tile_w, h), Image.LANCZOS))
    tw, th = tiles[0].size

    board = Image.new("RGB", (tw * 2, th * 2), (0, 0, 0))
    for i, im in enumerate(tiles):
        board.paste(im, ((i % 2) * tw, (i // 2) * th))

    out.parent.mkdir(parents=True, exist_ok=True)
    board.save(out, quality=88)
    return True


def _thumb(src: Path, out: Path) -> None:
    if not src.exists():
        return
    out.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(src) as im:
        im = im.convert("RGB")
        h = max(1, round(im.height * THUMB_WIDTH / im.width))
        im.resize((THUMB_WIDTH, h), Image.LANCZOS).save(out, quality=82)


def _frame_stats(frame_paths: list[Path]) -> tuple[float, float, float]:
    """返回 (平均亮度, 对比度, 帧间运动量)。缩小到 96×72 计算，够准且快。"""
    arrs: list[np.ndarray] = []
    for p in frame_paths:
        if not p.exists():
            continue
        with Image.open(p) as im:
            g = im.convert("L").resize((96, 72), Image.BILINEAR)
            arrs.append(np.asarray(g, dtype=np.float32))
    if not arrs:
        return 0.0, 0.0, 0.0
    stack = np.stack(arrs)
    brightness = float(stack.mean())
    contrast = float(stack.std())
    motion = float(np.abs(np.diff(stack, axis=0)).mean()) if len(arrs) > 1 else 0.0
    return brightness, contrast, motion


def process_shot(shot, video: Path) -> dict:
    """处理单个镜头，返回元数据记录。"""
    sid = shot.shot_id
    board = config.STORYBOARDS_DIR / f"{sid}.jpg"
    frames = [config.FRAMES_DIR / f"{sid}_{i}.jpg" for i in range(4)]

    status = "skip"
    if not board.exists():
        missing = [p for p in frames if not p.exists()]
        if missing:
            dur = shot.t1 - shot.t0
            for i, pos in enumerate(config.KEYFRAME_POSITIONS):
                fp = frames[i]
                if fp.exists():
                    continue
                if not _grab(video, shot.t0 + dur * pos, fp, config.FRAME_WIDTH):
                    continue
        existing = [p for p in frames if p.exists()]
        if not existing:
            return {"status": "fail", "shot_id": sid}
        if not _storyboard(existing, board):
            return {"status": "fail", "shot_id": sid}
        if len(existing) > 1:
            _thumb(existing[1], config.THUMBS_DIR / f"{sid}.jpg")
        else:
            _thumb(existing[0], config.THUMBS_DIR / f"{sid}.jpg")
        status = "ok"
    else:
        existing = [p for p in frames if p.exists()]
        if not existing:
            return {"status": "fail", "shot_id": sid}

    b, c, m = _frame_stats(existing)
    return {
        "status": status,
        "shot_id": sid,
        "p": shot.p,
        "t0": shot.t0,
        "t1": shot.t1,
        "dur": shot.dur,
        "n_frames": len(existing),
        "brightness": round(b, 1),
        "contrast": round(c, 1),
        "motion": round(m, 2),
    }


def build(
    episodes,
    shots,
    eps: set[int] | None = None,
    workers: int = 12,
    limit: int = 0,
) -> dict:
    """批量抽帧并度量。返回 {ok, skip, fail, meta_path}。"""
    videos = {e.p: config.VIDEO_DIR / e.file for e in episodes}
    todo = [s for s in shots if (not eps or s.p in eps) and s.p in videos]
    if limit:
        todo = todo[:limit]

    stats = {"ok": 0, "skip": 0, "fail": 0}
    records: list[dict] = []
    total = len(todo)
    print(f"待处理镜头 {total:,} 个，并发 {workers}\n")

    def job(s):
        return process_shot(s, videos[s.p])

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(job, s) for s in todo]
        done = 0
        for fut in as_completed(futures):
            done += 1
            try:
                rec = fut.result()
            except Exception:
                stats["fail"] += 1
                continue
            status = rec.get("status", "fail")
            stats[status] = stats.get(status, 0) + 1
            if status != "fail":
                records.append(rec)
            if done % 500 == 0 or done == total:
                print(f"  进度 {done:,}/{total:,} ({100*done/total:.1f}%)  "
                      f"ok={stats['ok']:,} skip={stats['skip']:,} fail={stats['fail']:,}")

    # 落盘元数据
    config.ensure_data_dir()
    meta_path = config.KEYFRAMES_JSONL
    records.sort(key=lambda r: r["shot_id"])
    with meta_path.open("w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    stats["meta_path"] = str(meta_path)
    stats["n_meta"] = len(records)
    return stats


def summarize_quality(records: list[dict] | None = None) -> str:
    """打印画面质量分布，用来确定过滤阈值。"""
    if records is None:
        if not config.KEYFRAMES_JSONL.exists():
            return "还没有 keyframes.jsonl"
        records = [
            json.loads(line)
            for line in config.KEYFRAMES_JSONL.open(encoding="utf-8")
        ]
    if not records:
        return "没有记录"

    def pct(vals: list[float], q: float) -> float:
        s = sorted(vals)
        return s[min(len(s) - 1, int(q * len(s)))]

    b = [r["brightness"] for r in records]
    c = [r["contrast"] for r in records]
    m = [r["motion"] for r in records]

    lines = [f"镜头数: {len(records):,}"]
    for name, vals in (("亮度", b), ("对比度", c), ("运动量", m)):
        lines.append(
            f"{name}: p5={pct(vals,0.05):.1f}  p25={pct(vals,0.25):.1f}  "
            f"中位={pct(vals,0.5):.1f}  p75={pct(vals,0.75):.1f}  p95={pct(vals,0.95):.1f}"
        )
    lines.append(f"暗场候选(亮度<30): {sum(1 for x in b if x < 30):,}")
    lines.append(f"低对比(对比度<15): {sum(1 for x in c if x < 15):,}")
    lines.append(f"近静止(运动量<1.5): {sum(1 for x in m if x < 1.5):,}")
    return "\n".join(lines)
