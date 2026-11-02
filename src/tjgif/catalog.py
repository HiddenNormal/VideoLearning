"""剧集目录：把文件名 + 片源体检结果合成 episodes.jsonl。

新数据源的命名非常规整：

    001   Puss Gets the Boot [1940].mp4
    098 wizard  The Flying Sorceress [1956].mp4

解析规则
    * 主键 = 文件名开头的编号（001 → 1），连续且唯一
    * 标题 = 编号和 [年份] 之间的内容（可能含多个空格）
    * 年份 = 方括号里的四位数字
    * 元数据来自 scripts/audit_tj.py 生成的 _audit.csv（ffprobe 结果）

解析失败的兜底：编号取开头连续数字，标题取整个文件名，年份留空。
"""
from __future__ import annotations

import csv
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from . import config

NAME_RE = re.compile(
    r"^(?P<num>\d{1,4})\s+(?P<title>.+?)\s*\[(?P<year>\d{4})\]\s*\.mp4$", re.I
)
NUM_RE = re.compile(r"^(\d{1,4})")

# NTSC / PAL DVD 常用变形像素尺寸（SAR ≠ 1，直接缩放会横向变形）
ANAMORPHIC_SIZES = {(720, 480), (704, 480), (720, 576), (704, 576)}


@dataclass
class Episode:
    p: int
    file: str
    title: str
    year: int | None = None
    duration_s: float = 0.0
    width: int = 0
    height: int = 0
    fps: float = 0.0
    codec: str = ""
    bitrate_kbps: float = 0.0
    size_mb: float = 0.0
    quality: str = "?"
    anamorphic: bool = False

    @property
    def aspect(self) -> float:
        return round(self.width / self.height, 3) if self.height else 0.0

    @property
    def gif_width(self) -> int:
        return config.GIF_WIDTH_BY_QUALITY.get(self.quality, 400)


def parse_name(path: Path) -> tuple[int, str, int | None]:
    """从文件名抠出 (编号, 标题, 年份)。"""
    m = NAME_RE.match(path.name)
    if m:
        return int(m.group("num")), m.group("title").strip(), int(m.group("year"))
    m2 = NUM_RE.match(path.name)
    if m2:
        return int(m2.group(1)), path.stem, None
    raise ValueError(f"无法从文件名解析编号: {path.name}")


def quality_of(bitrate_kbps: float) -> str:
    if bitrate_kbps >= 1200:
        return "A"
    if bitrate_kbps >= 600:
        return "B"
    if bitrate_kbps > 0:
        return "C"
    return "?"


def _read_audit(path: Path) -> dict[str, dict[str, str]]:
    if not path.exists():
        raise SystemExit(
            f"找不到片源体检文件：{path}\n先运行：python scripts/audit_tj.py"
        )
    with path.open(encoding="utf-8-sig", newline="") as f:
        return {row["file"]: row for row in csv.DictReader(f)}


def _num(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def build_catalog() -> list[Episode]:
    if not config.VIDEO_DIR.exists():
        raise SystemExit(
            f"片源目录不存在：{config.VIDEO_DIR}\n先运行：bash scripts/fetch_tj.sh"
        )

    files = sorted(config.VIDEO_DIR.glob("*.mp4"))
    if not files:
        raise SystemExit(f"{config.VIDEO_DIR} 里没有 mp4")

    audit = _read_audit(config.AUDIT_CSV)

    episodes: list[Episode] = []
    for path in files:
        p, title, year = parse_name(path)
        row = audit.get(path.name, {})
        width = int(_num(row.get("width")))
        height = int(_num(row.get("height")))
        kbps = _num(row.get("bitrate_kbps"))

        episodes.append(
            Episode(
                p=p,
                file=path.name,
                title=title,
                year=year,
                duration_s=_num(row.get("duration_s")),
                width=width,
                height=height,
                fps=_num(row.get("fps")),
                codec=row.get("codec", ""),
                bitrate_kbps=kbps,
                size_mb=_num(row.get("size_mb")),
                quality=quality_of(kbps),
                anamorphic=(width, height) in ANAMORPHIC_SIZES,
            )
        )

    episodes.sort(key=lambda e: e.p)

    ids = [e.p for e in episodes]
    if len(set(ids)) != len(ids):
        seen, repeated = set(), set()
        for p in ids:
            (repeated if p in seen else seen).add(p)
        raise SystemExit(f"编号重复，无法作为主键：{sorted(repeated)}")

    return episodes


def write_catalog(episodes: list[Episode]) -> Path:
    config.ensure_data_dir()
    out = config.EPISODES_JSONL
    with out.open("w", encoding="utf-8") as f:
        for ep in episodes:
            record = asdict(ep) | {"aspect": ep.aspect, "gif_width": ep.gif_width}
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    return out


def summarize(episodes: list[Episode]) -> str:
    if not episodes:
        return "没有剧集"

    total_s = sum(e.duration_s for e in episodes)
    by_quality: dict[str, int] = {}
    for e in episodes:
        by_quality[e.quality] = by_quality.get(e.quality, 0) + 1

    years = [e.year for e in episodes if e.year]
    ana = [e for e in episodes if e.anamorphic]
    missing = [e.p for e in episodes if e.duration_s <= 0]

    lines = [
        f"集数          : {len(episodes)}",
        f"总时长        : {total_s / 3600:.2f} 小时（{total_s:.0f} 秒）",
        f"总大小        : {sum(e.size_mb for e in episodes) / 1024:.2f} GB",
        f"平均单集      : {total_s / 60 / max(1, len(episodes)):.1f} 分钟",
        f"年份跨度      : {min(years)} ~ {max(years)}" if years else "年份跨度      : 未知",
        "画质分级      : "
        + " / ".join(f"{k} {v} 集" for k, v in sorted(by_quality.items())),
        f"变形像素集    : {len(ana)} 集"
        + (f"（{', '.join(str(e.p) for e in ana[:12])}）" if ana else ""),
    ]
    if missing:
        lines.append(f"⚠ 元数据缺失  : {len(missing)} 集（{missing[:10]}）")
    return "\n".join(lines)
