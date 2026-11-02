"""标签 schema、词表、prompt 构造、结果校验。

这个文件被远程打标脚本和本地建索引脚本共用，**改动要同步**。
不依赖任何第三方库，方便单独拷到别的机器。
"""
from __future__ import annotations

import json
import re

# ---------------------------------------------------------------- 受控词表

# 角色用英文 id，方便后续按角色硬过滤（检索时最可靠的字段）
CHARACTERS = [
    "tom", "jerry", "spike", "tuffy", "butch",
    "duck", "bird", "fish", "human", "other",
]

EMOTIONS = [
    "愤怒", "惊恐", "得意", "疼痛", "懵",
    "开心", "委屈", "阴险", "无语", "紧张",
]

# ---------------------------------------------------------------- prompt

SYSTEM_PROMPT = (
    "你是《猫和老鼠》动画的分镜标注员，负责把镜头画面转成便于检索的中文描述。"
    "你只输出 JSON，不输出任何解释。"
)

USER_PROMPT = """下面是一张拼图，包含**同一个镜头**的 4 张关键帧，按时间顺序排列：
左上 → 右上 → 左下 → 右下。

请只输出一个 JSON 对象，不要 markdown 代码块，不要任何解释。字段如下：

{{
  "characters": 画面里**实际出现**的主要角色，从 {characters} 中选，没出现就不要写，不要脑补,
  "setting": 场景，2~4 个中文字，如 厨房/客厅/院子/街道/地下室/太空/船上/雪地,
  "actions": 2~4 个中文动作短语，如 追/打/砸/摔倒/爆炸/逃跑/伪装/吃东西,
  "objects": 0~4 个显著道具，如 平底锅/铁砧/炸药/铁锤/棍子/盘子/门/梯子，没有就写空数组,
  "emotion": 0~3 个，从 {emotions} 中选，没有就写空数组,
  "outcome": 一句话说结果，如「汤姆被砸晕」；没有明显结果就写空字符串,
  "summary_zh": 一到两句中文，说清**谁对谁做了什么、结果如何**，要具体到道具和动作,
  "keywords_zh": 3~8 个便于搜索的中文关键词，包含口语说法,
  "gag_score": 0~1 的小数，表示这段作为表情包/梗图的可用性
}}

规则：
1. 只描述画面里真实可见的内容。不要根据《猫和老鼠》的常识补充你没看到的角色或情节。
2. 如果 4 帧无法判断动作，actions 只写画面里能确认的。
3. 输出必须是合法 JSON 对象，不能有注释、不能有尾部逗号。"""


def build_user_prompt() -> str:
    return USER_PROMPT.format(
        characters=json.dumps(CHARACTERS, ensure_ascii=False),
        emotions=json.dumps(EMOTIONS, ensure_ascii=False),
    )


# ---------------------------------------------------------------- 校验

REQUIRED = ("characters", "setting", "actions", "objects", "emotion",
            "outcome", "summary_zh", "keywords_zh", "gag_score")


def _as_str_list(value, limit: int) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        if not isinstance(item, str):
            continue
        s = item.strip()
        if s and s not in out:
            out.append(s)
    return out[:limit]


def normalize(raw: dict) -> dict:
    """把模型输出规整成稳定结构，容忍字段缺失/类型漂移。"""
    chars = [c for c in _as_str_list(raw.get("characters"), 6) if c in CHARACTERS]
    emotions = [e for e in _as_str_list(raw.get("emotion"), 4) if e in EMOTIONS]

    setting = raw.get("setting")
    setting = setting.strip()[:12] if isinstance(setting, str) else ""

    outcome = raw.get("outcome")
    outcome = outcome.strip()[:60] if isinstance(outcome, str) else ""

    summary = raw.get("summary_zh")
    summary = summary.strip() if isinstance(summary, str) else ""
    if len(summary) > 300:
        summary = summary[:300]

    keywords = _as_str_list(raw.get("keywords_zh"), 10)
    keywords = [k[:16] for k in keywords]

    try:
        gag = float(raw.get("gag_score", 0.0))
    except (TypeError, ValueError):
        gag = 0.0
    gag = max(0.0, min(1.0, gag))

    return {
        "characters": chars,
        "setting": setting,
        "actions": _as_str_list(raw.get("actions"), 6),
        "objects": _as_str_list(raw.get("objects"), 6),
        "emotion": emotions,
        "outcome": outcome,
        "summary_zh": summary,
        "keywords_zh": keywords,
        "gag_score": round(gag, 3),
    }


def is_usable(labels: dict) -> bool:
    """至少要有描述，否则这条标签没法用于检索。"""
    return len(labels.get("summary_zh", "")) >= 8


# ---------------------------------------------------------------- 解析

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


def parse_json_loose(text: str) -> dict | None:
    """从模型输出里尽量抠出 JSON 对象。

    模型常见的坏习惯：包 markdown 代码块、前后加解释、尾部逗号。
    """
    if not text:
        return None
    text = text.strip()

    # 1) 直接解析
    for candidate in (text, *_FENCE_RE.findall(text)):
        candidate = candidate.strip()
        if not candidate:
            continue
        try:
            obj = json.loads(candidate)
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass

    # 2) 截取第一个 { 到最后一个 }
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    snippet = text[start : end + 1]
    # 去掉尾部逗号
    snippet = re.sub(r",\s*([}\]])", r"\1", snippet)
    try:
        obj = json.loads(snippet)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        return None
