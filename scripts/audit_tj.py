#!/usr/bin/env python3
"""对新片源做体检：分辨率 / 帧率 / 码率 / 时长分布。

换源后旧的实测数据全部作废（原来是 161 集 624×464@25fps），
必须重新体检才能确定 GIF 参数、镜头切分阈值、画质分级。

用法
    python scripts/audit_tj.py                 # 默认扫 external/resources/tj
    python scripts/audit_tj.py --dir /data/tj
    python scripts/audit_tj.py --csv out.csv

产出
    external/resources/tj/_audit.csv     每个文件的元数据（在该目录下，已 gitignore）
    stdout                               分布汇总 + 异常清单
"""
from __future__ import annotations

import argparse
import csv
import json
import statistics
import subprocess
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DIR = REPO_ROOT / "external" / "resources" / "tj"


def probe(path: Path) -> dict | None:
    cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries",
        "stream=width,height,r_frame_rate,avg_frame_rate,bit_rate,codec_name:format=duration,size",
        "-of", "json",
        str(path),
    ]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if out.returncode != 0:
            return None
        data = json.loads(out.stdout or "{}")
    except Exception:
        return None

    stream = (data.get("streams") or [{}])[0]
    fmt = data.get("format") or {}

    def num(value):
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    def fps_of(value: str) -> float:
        if not value or "/" not in value:
            return 0.0
        a, b = value.split("/", 1)
        try:
            return float(a) / float(b) if float(b) else 0.0
        except ValueError:
            return 0.0

    fps = fps_of(stream.get("r_frame_rate", "")) or fps_of(stream.get("avg_frame_rate", ""))
    return {
        "width": int(num(stream.get("width"))),
        "height": int(num(stream.get("height"))),
        "fps": round(fps, 2),
        "codec": stream.get("codec_name", ""),
        "bitrate_kbps": round(num(stream.get("bit_rate")) / 1000, 0),
        "duration_s": round(num(fmt.get("duration")), 1),
        "size_mb": round(num(fmt.get("size")) / 1024 / 1024, 1),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", type=Path, default=DEFAULT_DIR)
    ap.add_argument("--csv", type=Path, default=None)
    args = ap.parse_args()

    files = sorted(args.dir.glob("*.mp4"))
    if not files:
        sys.exit(f"{args.dir} 里没有 mp4（先跑 scripts/fetch_tj.sh）")

    print(f"扫描 {len(files)} 个文件: {args.dir}\n")

    rows, failed = [], []
    for i, f in enumerate(files, 1):
        info = probe(f)
        if info is None:
            failed.append(f.name)
            continue
        rows.append({"file": f.name, **info})
        if i % 40 == 0 or i == len(files):
            print(f"  进度 {i}/{len(files)}")

    if not rows:
        sys.exit("所有文件都探测失败")

    csv_path = args.csv or (args.dir / "_audit.csv")
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    total_s = sum(r["duration_s"] for r in rows)
    total_gb = sum(r["size_mb"] for r in rows) / 1024
    durs = [r["duration_s"] for r in rows]

    print("\n" + "=" * 56)
    print(f"  文件数     : {len(rows)}（探测失败 {len(failed)}）")
    print(f"  总时长     : {total_s/3600:.2f} 小时")
    print(f"  总大小     : {total_gb:.2f} GB")
    print(f"  单集时长   : 平均 {statistics.fmean(durs)/60:.1f} 分钟  "
          f"中位 {statistics.median(durs)/60:.1f}  最短 {min(durs)/60:.1f}  最长 {max(durs)/60:.1f}")

    print("\n  分辨率分布:")
    for key, n in Counter(f"{r['width']}x{r['height']}" for r in rows).most_common():
        print(f"    {key:<12} {n:>4} 个")

    print("\n  帧率分布:")
    for key, n in Counter(f"{r['fps']:.2f}" for r in rows).most_common():
        print(f"    {key:<12} {n:>4} 个")

    print("\n  码率分级:")
    a = sum(1 for r in rows if r["bitrate_kbps"] >= 1200)
    b = sum(1 for r in rows if 600 <= r["bitrate_kbps"] < 1200)
    c = sum(1 for r in rows if r["bitrate_kbps"] < 600)
    print(f"    A (>1200kbps) {a:>4}   B (600~1200) {b:>4}   C (<600，GIF 会糊) {c:>4}")

    if c:
        print("\n  低码率文件（做 GIF 会明显发糊）:")
        for r in sorted(rows, key=lambda x: x["bitrate_kbps"])[: c if c <= 15 else 15]:
            print(f"    {r['bitrate_kbps']:>7.0f} kbps  {r['width']}x{r['height']}  {r['file']}")

    if failed:
        print(f"\n  ⚠ 探测失败 {len(failed)} 个（可能是下载没完成）:")
        for name in failed[:10]:
            print(f"    {name}")

    print(f"\n  CSV: {csv_path}")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
