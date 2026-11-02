"""全局配置。

路径都可以用环境变量覆盖：

    TJGIF_VIDEO   片源目录（默认 external/resources/tj）
    TJGIF_DATA    数据目录（默认 data/，已 gitignore）
    TJGIF_AUDIT   片源体检 CSV（默认 <片源>/_audit.csv）

数据源：archive.org 的《猫和老鼠》合集，240 个原始 mp4。
"""
from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _path(env: str, default: Path) -> Path:
    raw = os.environ.get(env)
    return Path(raw).expanduser() if raw else default


# 片源（gitignore，由 scripts/fetch_tj.sh 下载）
VIDEO_DIR = _path("TJGIF_VIDEO", REPO_ROOT / "external" / "resources" / "tj")

# 中间产物（gitignore，可由片源重建）
DATA_DIR = _path("TJGIF_DATA", REPO_ROOT / "data")

# 片源体检结果，由 scripts/audit_tj.py 生成
AUDIT_CSV = _path("TJGIF_AUDIT", VIDEO_DIR / "_audit.csv")

EPISODES_JSONL = DATA_DIR / "episodes.jsonl"
SHOTS_RAW_JSONL = DATA_DIR / "shots-raw.jsonl"
SHOTS_JSONL = DATA_DIR / "shots.jsonl"
KEYFRAMES_JSONL = DATA_DIR / "keyframes.jsonl"
CANDIDATES_JSONL = DATA_DIR / "candidates.jsonl"

FRAMES_DIR = DATA_DIR / "frames"
THUMBS_DIR = DATA_DIR / "thumbs"
STORYBOARDS_DIR = DATA_DIR / "storyboards"

# 关键帧
KEYFRAME_POSITIONS = (0.10, 0.35, 0.65, 0.90)  # 镜头内相对位置
FRAME_WIDTH = 384  # CLIP 输入 336，留余量
TILE_WIDTH = 384  # 故事板每格宽度

# 垃圾镜头过滤阈值
MIN_BRIGHTNESS = 30.0
MIN_CONTRAST = 15.0
MIN_MOTION = 1.5

# GIF 输出宽度：画质 C 级降一档，避免糊上加糊
GIF_WIDTH_BY_QUALITY = {"A": 480, "B": 480, "C": 400}


def ensure_data_dir() -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return DATA_DIR
