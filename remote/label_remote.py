#!/usr/bin/env python3
"""在远程 GPU 机器（如 H100）上给候选镜头打标。

输入
    <input-dir>/candidates.jsonl   候选镜头清单（本地生成）
    <input-dir>/storyboards/*.jpg  每镜头一张 2×2 关键帧拼图

输出
    <out>/labels.jsonl             每行一个镜头的结构化标签
    <out>/labels.failed.jsonl      解析失败/不可用的条目（含原始输出，便于排查）

用法
    # 冒烟测试：不下载模型，验证输入输出通路
    python label_remote.py --input-dir ./input --out ./out --backend mock --limit 50

    # 真跑（transformers 后端，最稳）
    python label_remote.py --input-dir ./input --out ./out \
        --model Qwen/Qwen2.5-VL-7B-Instruct --batch-size 8

    # 多卡并行：4 张卡各跑一个进程
    for i in 0 1 2 3; do
      CUDA_VISIBLE_DEVICES=$i python label_remote.py --input-dir ./input \
        --out ./out/shard$i --shard $i/4 &
    done; wait

设计要点
- **断点续跑**：输出文件里已有的 shot_id 直接跳过，跑一半断了重跑不会重复烧卡。
- **分片**：--shard i/n 按 shot_id 哈希取模切分，多个进程写不同文件，最后合并。
- **重试**：JSON 解析失败或描述太短的，单独再喂一次（最多 --retries 轮）。
- **逐条落盘**：buffering=1 逐行 flush，进程被杀也不丢已完成的部分。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import schema  # noqa: E402


# ---------------------------------------------------------------- 数据结构


@dataclass
class Item:
    shot_id: str
    p: int
    t0: float
    t1: float
    dur: float
    quality: str
    board: Path

    @property
    def id_num(self) -> int:
        """给分片用的稳定数值。"""
        return sum(ord(c) for c in self.shot_id)


def load_items(input_dir: Path, limit: int, shard: str) -> tuple[list[Item], dict]:
    cand_path = input_dir / "candidates.jsonl"
    board_dir = input_dir / "storyboards"
    if not cand_path.exists():
        sys.exit(f"找不到 {cand_path}")
    if not board_dir.exists():
        sys.exit(f"找不到 {board_dir}")

    total, missing = 0, 0
    items: list[Item] = []
    shard_idx, shard_total = parse_shard(shard)

    with cand_path.open(encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            rec = json.loads(line)
            total += 1
            board = board_dir / f"{rec['shot_id']}.jpg"
            if not board.exists():
                missing += 1
                continue
            item = Item(
                shot_id=rec["shot_id"],
                p=int(rec["p"]),
                t0=float(rec["t0"]),
                t1=float(rec["t1"]),
                dur=float(rec["dur"]),
                quality=rec.get("quality", "?"),
                board=board,
            )
            if item.id_num % shard_total != shard_idx:
                continue
            items.append(item)

    items.sort(key=lambda it: it.shot_id)
    if limit:
        items = items[:limit]

    stats = {"total": total, "missing_board": missing, "picked": len(items)}
    return items, stats


def parse_shard(text: str) -> tuple[int, int]:
    if not text or "/" not in text:
        return 0, 1
    idx, total = text.split("/", 1)
    idx, total = int(idx), int(total)
    if not (0 <= idx < total):
        sys.exit(f"--shard 参数非法: {text}（应形如 0/4）")
    return idx, total


def load_done(out_path: Path) -> set[str]:
    done: set[str] = set()
    if not out_path.exists():
        return done
    with out_path.open(encoding="utf-8") as f:
        for line in f:
            try:
                done.add(json.loads(line)["shot_id"])
            except Exception:
                continue
    return done


# ---------------------------------------------------------------- 后端


class MockBackend:
    """不加载模型，生成假标签，用来验证输入输出通路。"""

    name = "mock"

    SAMPLES = [
        (["tom", "jerry"], "客厅", ["追", "摔倒在地"], ["花瓶"], ["惊恐"],
         "汤姆追杰瑞时被花瓶绊倒，摔了个四脚朝天", 0.7),
        (["tom"], "厨房", ["举锅", "砸"], ["平底锅"], ["愤怒"],
         "汤姆举着平底锅想砸杰瑞，表情凶狠", 0.8),
        (["jerry"], "院子", ["逃跑", "回头"], ["奶酪"], ["得意"],
         "杰瑞抱着奶酪从汤姆身边跑过，回头露出得意的笑", 0.75),
        (["spike"], "狗屋", ["吼叫", "抓"], [], ["愤怒"],
         "斯派克从狗屋里冲出来，一把抓住汤姆的脖子大吼", 0.85),
        (["tom", "jerry"], "街道", ["爆炸"], ["炸药"], ["懵"],
         "炸药在汤姆手里爆炸，汤姆变成一脸焦黑，杰瑞在旁边偷笑", 0.9),
    ]

    def run(self, items):
        for it in items:
            t0 = time.time()
            chars, setting, actions, objects, emo, summary, gag = self.SAMPLES[
                it.id_num % len(self.SAMPLES)
            ]
            labels = {
                "characters": chars,
                "setting": setting,
                "actions": actions,
                "objects": objects,
                "emotion": emo,
                "outcome": "",
                "summary_zh": summary,
                "keywords_zh": [setting] + actions + objects,
                "gag_score": gag,
            }
            yield it, json.dumps(labels, ensure_ascii=False), round(time.time() - t0, 3)


class TransformersBackend:
    name = "transformers"

    def __init__(self, model_id: str, batch_size: int, max_new_tokens: int):
        self.model_id = model_id
        self.batch_size = batch_size
        self.max_new_tokens = max_new_tokens
        self._model = None
        self._processor = None
        self._prompt = schema.build_user_prompt()

    def _load(self):
        import torch
        from transformers import AutoModelForImageTextToText, AutoProcessor

        print(f"[loading] {self.model_id} ...", flush=True)
        t0 = time.time()
        dtype = torch.bfloat16 if torch.cuda.is_available() else torch.float32
        try:
            model = AutoModelForImageTextToText.from_pretrained(
                self.model_id, dtype=dtype, device_map="auto"
            )
        except TypeError:  # 老版本 transformers 用 torch_dtype
            model = AutoModelForImageTextToText.from_pretrained(
                self.model_id, torch_dtype=dtype, device_map="auto"
            )
        model.eval()
        processor = AutoProcessor.from_pretrained(self.model_id)
        tok = getattr(processor, "tokenizer", None)
        if tok is not None:
            tok.padding_side = "left"
        print(f"[loading] 完成，耗时 {time.time()-t0:.1f}s，"
              f"device={getattr(model, 'device', '?')}", flush=True)
        self._model, self._processor = model, processor

    def _generate(self, paths: list[Path]) -> list[str]:
        import torch
        from PIL import Image

        model, processor = self._model, self._processor
        images = []
        for p in paths:
            with Image.open(p) as im:
                images.append(im.convert("RGB"))

        messages = [
            [{"role": "user",
              "content": [{"type": "image"},
                          {"type": "text", "text": self._prompt}]}]
            for _ in images
        ]
        texts = [
            processor.apply_chat_template(m, tokenize=False, add_generation_prompt=True)
            for m in messages
        ]
        batch = processor(text=texts, images=images, padding=True, return_tensors="pt")
        if "pixel_values" in batch and hasattr(model, "dtype"):
            batch["pixel_values"] = batch["pixel_values"].to(model.dtype)
        batch = batch.to(model.device)

        with torch.inference_mode():
            out = model.generate(
                **batch,
                max_new_tokens=self.max_new_tokens,
                do_sample=False,
            )
        prompt_len = batch["input_ids"].shape[1]
        return processor.batch_decode(out[:, prompt_len:], skip_special_tokens=True)

    def run(self, items):
        if self._model is None:
            self._load()
        for start in range(0, len(items), self.batch_size):
            batch = items[start : start + self.batch_size]
            t0 = time.time()
            try:
                texts = self._generate([it.board for it in batch])
            except Exception as exc:  # 整批失败就逐条重来，避免一条坏数据毁一批
                print(f"[warn] 批处理失败（{exc}），改为逐条", flush=True)
                texts = []
                for it in batch:
                    try:
                        texts.append(self._generate([it.board])[0])
                    except Exception as exc2:
                        texts.append(f"<error: {exc2}>")
            elapsed = round((time.time() - t0) / max(1, len(batch)), 3)
            for it, text in zip(batch, texts):
                yield it, text, elapsed


class VLLMBackend:
    name = "vllm"

    def __init__(self, model_id: str, batch_size: int, max_new_tokens: int):
        self.model_id = model_id
        self.batch_size = batch_size
        self.max_new_tokens = max_new_tokens
        self._llm = None
        self._sp = None
        self._prompt = schema.build_user_prompt()

    def _load(self):
        from vllm import LLM, SamplingParams

        print(f"[loading] vLLM {self.model_id} ...", flush=True)
        t0 = time.time()
        self._llm = LLM(
            model=self.model_id,
            dtype="bfloat16",
            limit_mm_per_prompt={"image": 1},
            trust_remote_code=True,
        )
        self._sp = SamplingParams(temperature=0.0, max_tokens=self.max_new_tokens)
        print(f"[loading] 完成，耗时 {time.time()-t0:.1f}s", flush=True)

    def run(self, items):
        if self._llm is None:
            self._load()
        for start in range(0, len(items), self.batch_size):
            batch = items[start : start + self.batch_size]
            t0 = time.time()
            messages = [
                [{"role": "user",
                  "content": [
                      {"type": "image_url",
                       "image_url": {"url": f"file://{it.board.resolve()}"}},
                      {"type": "text", "text": self._prompt},
                  ]}]
                for it in batch
            ]
            try:
                outs = self._llm.chat(messages, sampling_params=self._sp)
                texts = [o.outputs[0].text for o in outs]
            except Exception as exc:
                print(f"[warn] vLLM 批处理失败（{exc}），改为逐条", flush=True)
                texts = []
                for msg in messages:
                    try:
                        texts.append(
                            self._llm.chat([msg], sampling_params=self._sp)[0]
                            .outputs[0].text
                        )
                    except Exception as exc2:
                        texts.append(f"<error: {exc2}>")
            elapsed = round((time.time() - t0) / max(1, len(batch)), 3)
            for it, text in zip(batch, texts):
                yield it, text, elapsed


def make_backend(args):
    if args.backend == "mock":
        return MockBackend()
    if args.backend == "vllm":
        return VLLMBackend(args.model, args.batch_size, args.max_new_tokens)
    return TransformersBackend(args.model, args.batch_size, args.max_new_tokens)


# ---------------------------------------------------------------- 主流程


def main() -> int:
    ap = argparse.ArgumentParser(description="远程 GPU 打标")
    ap.add_argument("--input-dir", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--model", default="Qwen/Qwen2.5-VL-7B-Instruct")
    ap.add_argument("--backend", default="transformers",
                    choices=["transformers", "vllm", "mock"])
    ap.add_argument("--batch-size", type=int, default=8)
    ap.add_argument("--max-new-tokens", type=int, default=400)
    ap.add_argument("--retries", type=int, default=1)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--shard", default="0/1", help="形如 0/4，多卡时各进程用不同值")
    ap.add_argument("--device", default="", help="仅作记录，CUDA_VISIBLE_DEVICES 更可靠")
    args = ap.parse_args()

    items, load_stats = load_items(args.input_dir, args.limit, args.shard)
    print(f"候选清单 : {load_stats['total']:,} 条")
    print(f"缺故事板 : {load_stats['missing_board']:,} 条（跳过）")
    print(f"本次处理 : {load_stats['picked']:,} 条  分片={args.shard}")

    args.out.mkdir(parents=True, exist_ok=True)
    out_path = args.out / "labels.jsonl"
    failed_path = args.out / "labels.failed.jsonl"

    done = load_done(out_path)
    todo = [it for it in items if it.shot_id not in done]
    if done:
        print(f"断点续跑 : 已完成 {len(done):,} 条，剩余 {len(todo):,} 条")
    if not todo:
        print("没有需要处理的了")
        return 0

    backend = make_backend(args)
    print(f"后端     : {backend.name}   模型: {getattr(args, 'model', '-')}\n")

    failures: dict[str, dict] = {}
    ok = 0
    failed_attempts = 0
    start_all = time.time()

    pending = todo
    for attempt in range(args.retries + 1):
        if not pending:
            break
        if attempt:
            print(f"\n[retry] 第 {attempt} 轮重试 {len(pending):,} 条", flush=True)
        round_start = time.time()
        with out_path.open("a", encoding="utf-8") as f_out:
            for n, (item, raw, elapsed) in enumerate(backend.run(pending), 1):
                obj = schema.parse_json_loose(raw)
                labels = schema.normalize(obj) if obj else None
                if labels and schema.is_usable(labels):
                    record = {
                        "shot_id": item.shot_id,
                        "p": item.p,
                        "t0": item.t0,
                        "t1": item.t1,
                        "dur": item.dur,
                        "quality": item.quality,
                        "labels": labels,
                        "model": args.model,
                        "backend": backend.name,
                        "elapsed_s": elapsed,
                    }
                    f_out.write(json.dumps(record, ensure_ascii=False) + "\n")
                    failures.pop(item.shot_id, None)
                    ok += 1
                else:
                    failed_attempts += 1
                    failures[item.shot_id] = {
                        "shot_id": item.shot_id,
                        "reason": "no_json" if obj is None else "unusable",
                        "raw": (raw or "")[:800],
                    }

                if n % 20 == 0 or n == len(pending):
                    done_n = ok
                    total_all = len(todo)
                    speed = round((time.time() - round_start) / n, 3)
                    remain = max(0, total_all - done_n)
                    eta_min = round(speed * remain / 60, 1)
                    print(
                        f"  {done_n:,}/{total_all:,}  "
                        f"{speed:.2f}s/条  ETA {eta_min} 分钟  "
                        f"失败累计 {len(failures):,}",
                        flush=True,
                    )

        pending = [it for it in pending if it.shot_id in failures]
        if not pending:
            break

    if failures:
        with failed_path.open("w", encoding="utf-8") as f:
            for rec in failures.values():
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    total_s = time.time() - start_all
    print("\n" + "=" * 56)
    print(f"成功写入   : {ok:,} 条  →  {out_path}")
    print(f"仍失败     : {len(failures):,} 条  →  {failed_path if failures else '(无)'}")
    print(f"总耗时     : {total_s/60:.1f} 分钟")
    if todo:
        print(f"平均速度   : {total_s/max(1,ok):.2f} 秒/条")

    # 抽样给人工看质量
    if ok:
        print("\n=== 抽样 3 条 ===")
        with out_path.open(encoding="utf-8") as f:
            lines = f.readlines()
        for line in lines[-3:]:
            rec = json.loads(line)
            lb = rec["labels"]
            print(f"  {rec['shot_id']}  {rec['t0']:.1f}-{rec['t1']:.1f}s")
            print(f"    摘要: {lb['summary_zh']}")
            print(f"    角色: {lb['characters']}  动作: {lb['actions']}  "
                  f"道具: {lb['objects']}  情绪: {lb['emotion']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
