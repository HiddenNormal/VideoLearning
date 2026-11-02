#!/usr/bin/env python3
"""从 archive.org 下载《猫和老鼠》合集。

数据源
    https://archive.org/download/tom-and-jerry-cartoon-collection

关键规则（按需求）
    * 只取原始 mp4：**排除 `.ia.mp4`**（archive.org 自动转码的派生文件）
    * 文件名、目录结构保持原样
    * 用 archive.org 元数据里的 md5 做完整性校验

用法
    python scripts/fetch_tj.py --list                 # 只列出会下哪些、多大
    python scripts/fetch_tj.py --limit 3              # 先下 3 个试试
    python scripts/fetch_tj.py                        # 全量下载（断点续传）
    python scripts/fetch_tj.py --workers 6 --no-verify

设计
    * 元数据走 JSON API，不解析 HTML（网页结构会变，API 稳定）
    * 下载用 curl（`-C -` 断点续传、`--retry` 自动重试），比手写 Range 稳
    * 已存在且大小一致的文件直接跳过，中断后重跑不会重复下载
    * md5 校验失败的文件会被删除并重下
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ITEM = "tom-and-jerry-cartoon-collection"
META_URL = f"https://archive.org/metadata/{ITEM}"
BASE_URL = f"https://archive.org/download/{ITEM}"

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DEST = REPO_ROOT / "external" / "resources" / "tj"


# ---------------------------------------------------------------- 元数据

def fetch_metadata(timeout: int = 60) -> dict:
    req = urllib.request.Request(META_URL, headers={"User-Agent": "tjgif-fetch/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


def select_originals(meta: dict) -> list[dict]:
    """挑出原始 mp4（排除 .ia.mp4 派生文件）。"""
    picked = []
    skipped_derived = 0
    for f in meta.get("files", []):
        name = f.get("name", "")
        lower = name.lower()
        if not lower.endswith(".mp4"):
            continue
        if lower.endswith(".ia.mp4"):
            skipped_derived += 1
            continue
        picked.append(
            {
                "name": name,
                "size": int(f.get("size", 0) or 0),
                "md5": (f.get("md5") or "").strip().lower(),
            }
        )
    picked.sort(key=lambda x: x["name"])
    return picked, skipped_derived


def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


# ---------------------------------------------------------------- 下载

def remote_url(name: str) -> str:
    # 逐段编码，保留目录分隔符
    return f"{BASE_URL}/" + urllib.parse.quote(name, safe="/")


def md5_of(path: Path, chunk: int = 8 * 1024 * 1024) -> str:
    h = hashlib.md5()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def download_one(entry: dict, dest: Path, verify: bool) -> dict:
    name = entry["name"]
    target = dest / name
    target.parent.mkdir(parents=True, exist_ok=True)

    # 已完整存在则跳过
    if target.exists() and entry["size"] and target.stat().st_size == entry["size"]:
        return {"name": name, "status": "skip", "bytes": entry["size"]}

    started = time.time()
    url = remote_url(name)
    last_error = ""

    # archive.org 连接不稳定，单次 curl 经常断（error 18 partial file）。
    # 外层再包一层重试，配合 -C - 续传，能把大文件啃下来。
    for attempt in range(1, 4):
        cmd = [
            "curl", "-L", "--fail", "--silent", "--show-error",
            "-C", "-",                          # 断点续传
            "--retry", "5", "--retry-delay", "3",
            "--retry-all-errors",               # 连 partial file 也重试
            "--speed-limit", "51200",           # 低于 50KB/s
            "--speed-time", "45",               # 持续 45 秒就掐断重连
            "--max-time", "300",                # 单次传输 5 分钟硬上限
                                                # （archive.org 限流时连接会"假活"，
                                                #   短超时 + 续传比长超时更高效）
            "--connect-timeout", "20",
            "-o", str(target),
            url,
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        got = target.stat().st_size if target.exists() else 0

        if proc.returncode == 33:               # 服务器不支持 Range：文件已完整
            pass
        elif proc.returncode != 0:
            last_error = (proc.stderr or "").strip()[:200]
            time.sleep(2 * attempt)
            continue

        if entry["size"] and got != entry["size"]:
            last_error = f"size {got} != {entry['size']}"
            time.sleep(2 * attempt)
            continue
        break
    else:
        return {
            "name": name,
            "status": "fail",
            "bytes": target.stat().st_size if target.exists() else 0,
            "error": last_error or "多次重试仍失败",
            "seconds": round(time.time() - started, 1),
        }

    got = target.stat().st_size if target.exists() else 0

    if verify and entry.get("md5"):
        actual = md5_of(target)
        if actual != entry["md5"]:
            target.unlink(missing_ok=True)
            return {
                "name": name,
                "status": "md5_mismatch",
                "bytes": got,
                "error": f"{actual[:12]} != {entry['md5'][:12]}（已删除，重跑会重下）",
                "seconds": round(time.time() - started, 1),
            }

    seconds = time.time() - started
    return {
        "name": name,
        "status": "ok",
        "bytes": got,
        "seconds": round(seconds, 1),
        "speed": round(got / seconds / 1024, 1) if seconds else 0,
    }


# ---------------------------------------------------------------- 主流程

def main() -> int:
    ap = argparse.ArgumentParser(description="从 archive.org 下载猫和老鼠原始 mp4")
    ap.add_argument("--dest", type=Path, default=DEFAULT_DEST, help=f"默认 {DEFAULT_DEST}")
    ap.add_argument("--workers", type=int, default=4, help="并发下载数（archive.org 建议 ≤6）")
    ap.add_argument("--limit", type=int, default=0, help="只下前 N 个（调试）")
    ap.add_argument("--list", action="store_true", help="只列出文件，不下载")
    ap.add_argument("--status", action="store_true", help="只检查完整性并退出")
    ap.add_argument("--no-verify", action="store_true", help="跳过 md5 校验")
    args = ap.parse_args()

    if shutil.which("curl") is None:
        sys.exit("找不到 curl")

    print(f"数据源: {META_URL}")
    meta = fetch_metadata()
    files, skipped = select_originals(meta)

    if not files:
        sys.exit("元数据里没找到任何原始 mp4")

    total = sum(f["size"] for f in files)
    print(f"条目名: {meta.get('metadata', {}).get('identifier', ITEM)}")
    print(f"原始 mp4: {len(files)} 个，共 {human(total)}")
    print(f"已排除 .ia.mp4 派生文件: {skipped} 个")
    print(f"目标目录: {args.dest}")
    print()

    if args.limit:
        files = files[: args.limit]
        total = sum(f["size"] for f in files)
        print(f"（--limit 生效：只处理前 {len(files)} 个，{human(total)}）\n")

    if args.status:
        complete = partial = missing = 0
        for f in files:
            target = args.dest / f["name"]
            if not target.exists():
                missing += 1
            elif f["size"] and target.stat().st_size == f["size"]:
                complete += 1
            else:
                partial += 1
        print(f"complete={complete} partial={partial} missing={missing} total={len(files)}")
        return 0

    if args.list:
        print(f"{'大小':>10}  文件名")
        for f in files:
            print(f"{human(f['size']):>10}  {f['name']}")
        print()
        print(f"合计 {len(files)} 个，{human(total)}")
        return 0

    args.dest.mkdir(parents=True, exist_ok=True)
    verify = not args.no_verify
    ok = skip = fail = 0
    done_bytes = 0
    failures: list[dict] = []
    started = time.time()

    print(f"开始下载（并发 {args.workers}，md5 校验 {'开' if verify else '关'}）\n")
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(download_one, f, args.dest, verify): f for f in files}
        for i, fut in enumerate(as_completed(futures), 1):
            res = fut.result()
            status = res["status"]
            if status == "ok":
                ok += 1
                done_bytes += res["bytes"]
            elif status == "skip":
                skip += 1
                # 跳过的不计入下载量与速度，否则 ETA 会算出几万 GB/s
            else:
                fail += 1
                failures.append(res)
            mark = {"ok": "✓", "skip": "=", "fail": "✗", "size_mismatch": "✗",
                    "md5_mismatch": "✗"}.get(status, "?")
            extra = f"  {res.get('error', '')}" if res.get("error") else ""
            print(f"  [{i:>3}/{len(files)}] {mark} {res['name'][:70]}{extra}")

            if i % 10 == 0 or i == len(files):
                elapsed = time.time() - started
                speed = done_bytes / elapsed if elapsed else 0
                pct = 100 * i / len(files)
                print(f"        进度 {pct:.1f}%  已下 {human(done_bytes)}  "
                      f"{human(speed)}/s  {elapsed/60:.1f} 分钟")

    elapsed = time.time() - started
    print()
    print("=" * 56)
    print(f"  成功   : {ok}")
    print(f"  跳过   : {skip}（已存在）")
    print(f"  失败   : {fail}")
    print(f"  总大小 : {human(done_bytes)}")
    print(f"  耗时   : {elapsed/60:.1f} 分钟")
    if failures:
        print("\n  失败清单（重跑本脚本会续传重试）:")
        for f in failures[:20]:
            print(f"    - {f['name']}  {f.get('error', '')}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
