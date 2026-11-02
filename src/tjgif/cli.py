"""命令行入口。

    python -m tjgif info         查看配置
    python -m tjgif catalog      构建 episodes.jsonl
    python -m tjgif shots        镜头后处理
    python -m tjgif keyframes    抽关键帧 + 故事板 + 质量度量
    python -m tjgif candidates   过滤出可打标的候选镜头
"""
from __future__ import annotations

import argparse

from . import candidates, catalog, config, keyframes, shots


def _status(path) -> str:
    return "存在" if path.exists() else "不存在"


def cmd_info(_args) -> int:
    print(f"片源目录 : {config.VIDEO_DIR}  [{_status(config.VIDEO_DIR)}]")
    print(f"体检 CSV : {config.AUDIT_CSV}  [{_status(config.AUDIT_CSV)}]")
    print(f"数据目录 : {config.DATA_DIR}  [{_status(config.DATA_DIR)}]")
    print()
    print(f"产物位置 : {config.DATA_DIR}")
    for name, path in (
        ("episodes.jsonl", config.EPISODES_JSONL),
        ("shots.jsonl", config.SHOTS_JSONL),
        ("keyframes.jsonl", config.KEYFRAMES_JSONL),
        ("candidates.jsonl", config.CANDIDATES_JSONL),
    ):
        print(f"  {name:<18} [{_status(path)}]")
    return 0


def cmd_catalog(_args) -> int:
    episodes = catalog.build_catalog()
    out = catalog.write_catalog(episodes)
    print(catalog.summarize(episodes))
    print(f"\n写出: {out}")
    return 0


def cmd_shots(_args) -> int:
    episodes = catalog.build_catalog()
    data = shots.build(episodes)
    out = shots.write(data)
    print(shots.summarize(data))
    print(f"\n写出: {out}")
    return 0


def cmd_keyframes(args) -> int:
    episodes = catalog.build_catalog()
    data = shots.build(episodes)
    eps = {int(x) for x in args.eps.split(",") if x.strip()} if args.eps else None
    stats = keyframes.build(
        episodes, data, eps=eps, workers=args.workers, limit=args.limit
    )
    print(f"\n完成: ok={stats['ok']:,}  skip={stats['skip']:,}  fail={stats['fail']:,}")
    print(f"元数据: {stats['meta_path']}（{stats['n_meta']:,} 条）\n")
    print("=== 画面质量分布 ===")
    print(keyframes.summarize_quality())
    return 0


def cmd_candidates(_args) -> int:
    episodes = catalog.build_catalog()
    data = shots.build(episodes)
    cands, stats = candidates.build(data)
    out = candidates.write(cands)
    print(candidates.summarize(cands, stats))
    print(f"\n写出: {out}")
    if stats["bounds"]:
        print("\n各集正片起点（前 8 集）：")
        for p, (lo, hi) in list(stats["bounds"].items())[:8]:
            print(f"  P{p:<4} 正片 {lo:>6.1f}s ~ {hi:>6.1f}s")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="tjgif", description="猫和老鼠语义搜片动图生成器")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_info = sub.add_parser("info", help="显示当前配置")
    p_info.set_defaults(func=cmd_info)

    p_cat = sub.add_parser("catalog", help="构建剧集目录 episodes.jsonl")
    p_cat.set_defaults(func=cmd_catalog)

    p_sh = sub.add_parser("shots", help="镜头后处理，生成 shots.jsonl")
    p_sh.set_defaults(func=cmd_shots)

    p_kf = sub.add_parser("keyframes", help="抽关键帧 + 故事板 + 缩略图")
    p_kf.add_argument("--eps", default="", help="只处理指定集，逗号分隔，如 1,3,5")
    p_kf.add_argument("--workers", type=int, default=12, help="并发数")
    p_kf.add_argument("--limit", type=int, default=0, help="只处理前 N 个镜头（调试）")
    p_kf.set_defaults(func=cmd_keyframes)

    p_cd = sub.add_parser("candidates", help="过滤出可打标的候选镜头")
    p_cd.set_defaults(func=cmd_candidates)

    args = ap.parse_args(argv)
    return args.func(args)
