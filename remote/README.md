# 远程打标包（H100 / 任意 NVIDIA GPU 机器）

把「13,879 个镜头写中文描述」这一步搬到远程 GPU 上跑，本机只负责后面建索引。

**全程不需要 sudo，不污染系统 Python。**

---

## 一页流程

```
[WSL 本机]                          [H100 单卡]
  ① bash pack_inputs.sh  ──scp──▶  ② bash setup_h100.sh
     打包 ~970MB 输入                ③ mock 冒烟 → compare_models 对比 7B/32B
                                     ④ 全量跑（20 分钟 ~ 2 小时）
  ⑥ 取回 labels.jsonl  ◀──scp─────   ⑤ out/labels.jsonl
```

---

## 包内容

| 文件 | 作用 |
|---|---|
| `setup_h100.sh` | 装 uv + venv + torch/transformers（可选 vLLM），自动自检 GPU |
| `label_remote.py` | 打标主脚本，支持 transformers / vLLM / mock 三种后端 |
| `schema.py` | 标签 schema、prompt、JSON 容错解析 |
| `merge_shards.sh` | 多卡分片结果合并去重（单卡用不到，留着备用） |
| `compare_models.sh` | 同批镜头 A/B 对比两个模型，用来决定全量用哪个 |
| `candidates.jsonl` | 13,879 个候选镜头（本地已生成） |
| `storyboards/` | 每个镜头一张 2×2 关键帧拼图，约 970MB |

---

## 步骤 0：机器要求

- Ubuntu 20.04+ / 任意 Linux
- **单张** NVIDIA GPU 即可，显存 ≥ 16GB
  - 7B 模型 bf16 约需 16GB
  - 32B 模型 bf16 约需 70GB（H100 80GB 刚好能跑，batch 压到 2）
- 磁盘 ≥ 100GB（7B ≈ 16GB、32B ≈ 64GB，做模型对比时两个都要下）
- 能访问 HuggingFace（境内可用 `--hf-mirror`）

先确认能看到 GPU：

```bash
nvidia-smi
```

---

## 步骤 1：把输入传到 H100

**在 WSL 本机执行：**

```bash
cd /mnt/e/Repos/tom-jerry-gif/remote
bash pack_inputs.sh
# 产出 ~/tjgif-label-input.tar.gz （约 970MB）
```

传到远程（推荐 rsync，断点续传）：

```bash
rsync -avP ~/tjgif-label-input.tar.gz 用户名@h100地址:~/
ssh 用户名@h100地址 'mkdir -p ~/tjgif && tar xzf ~/tjgif-label-input.tar.gz -C ~/tjgif'
```

> 网络慢的话也可以用 `scp`，或者直接 `rsync -avP` 同步打包前的目录（脚本会提示路径）。

---

## 步骤 2：装环境

**在 H100 上执行：**

```bash
cd ~/tjgif
bash setup_h100.sh              # 基础版（transformers 后端）
# 或
bash setup_h100.sh --with-vllm  # 加 vLLM，吞吐高 2~3 倍
# 境内机器再加 --hf-mirror
```

装完会打印 torch 版本、CUDA 是否可用、每张卡的型号和显存。

---

## 步骤 3：冒烟测试（不下载模型，30 秒）

```bash
source ~/tjgif-venv/bin/activate
cd ~/tjgif

python label_remote.py --input-dir ./ --out ./out-mock --backend mock --limit 50
```

预期：50 条全部成功，抽样 3 条能打印出假标签。这一步只验证输入输出通路，不占用 GPU。

---

## 步骤 4：小样本 + 模型对比（**关键，先定模型再全量**）

**单卡 H100（80GB）有个优势：能直接跑 32B 模型。** 但 32B 比 7B 慢 3~4 倍，
所以先花 10 分钟比一下，别盲目上大的。

```bash
bash compare_models.sh ./ 20
```

它会在同一批 20 个镜头上跑两个模型，并排打印摘要、角色、道具，最后给出统计：

```
[1] P001_0054  190.8-195.0s
  A 摘要 : 汤姆举着平底锅追打杰瑞，杰瑞躲到桌子底下
  B 摘要 : 汤姆双手高举平底锅冲向杰瑞，杰瑞已经钻进桌底露出尾巴
  A 角色 : ['tom', 'jerry']   道具: ['平底锅']
  B 角色 : ['tom', 'jerry']   道具: ['平底锅', '桌子']

A (Qwen/Qwen2.5-VL-7B-Instruct)
  平均摘要 38 字   有角色 100%   有道具 85%
B (Qwen/Qwen3-VL-32B-Instruct)
  平均摘要 47 字   有角色 100%   有道具 92%
```

**判断标准就三条**：
1. 摘要能不能说清「谁对谁做了什么、结果如何」，而不是「两只动物在互动」
2. 汤姆 / 杰瑞 / 斯派克 有没有认错（这是硬伤，认错就没法检索）
3. 道具识别对不对

7B 够用就用 7B（全量 20~35 分钟）；明显更糊就上 32B（全量 1~2 小时）。
两个模型都会下载（7B 约 16GB，32B 约 64GB），机器盘要留够 100GB。

> 单独跑某个模型：`python label_remote.py --input-dir ./ --out ./out --model <模型名> --limit 20`

---

## 步骤 5：全量跑（单卡）

确定模型后，一条命令跑完全部 13,879 条：

```bash
nohup python label_remote.py \
  --input-dir ./ --out ./out \
  --model Qwen/Qwen2.5-VL-7B-Instruct \
  --batch-size 8 > label.log 2>&1 &

tail -f label.log
```

换成 32B 时把 `--batch-size` 降到 2（显存要留 KV cache）：

```bash
nohup python label_remote.py --input-dir ./ --out ./out \
  --model Qwen/Qwen3-VL-32B-Instruct --batch-size 2 > label.log 2>&1 &
```

日志每 20 条刷新一次，带 ETA：

```
  1,240/13,879  2.31s/条  ETA 48.6 分钟  失败累计 3
```

### 想更快？装 vLLM 后端

`setup_h100.sh --with-vllm` 装好之后（显存 40GB+ 可用）：

```bash
nohup python label_remote.py --input-dir ./ --out ./out \
  --backend vllm --model Qwen/Qwen2.5-VL-7B-Instruct \
  --batch-size 32 > label.log 2>&1 &
```

吞吐能提高 2~3 倍。vLLM 装不上也不影响——transformers 后端一样能跑完，只是慢些。

### 跑断了怎么办

直接重跑同一条命令即可。脚本会读已有的 `labels.jsonl`，**跳过已完成的 shot_id**，只补剩下的。

### 中途想看进度

```bash
wc -l out/labels.jsonl        # 已完成条数
tail -5 label.log             # 最近日志
```

---

## 步骤 6：取回结果

```bash
# 在 WSL 本机执行
rsync -avP 用户名@h100地址:~/tjgif/out/labels.jsonl ~/tj-gif/data/
```

本地接着跑建索引（下一步开发）：

```bash
cd /mnt/e/Repos/tom-jerry-gif
PYTHONPATH=$PWD/src python -m tjgif index
```

---

## 参数说明

| 参数 | 默认 | 说明 |
|---|---|---|
| `--input-dir` | 必填 | 含 `candidates.jsonl` + `storyboards/` 的目录 |
| `--out` | 必填 | 输出目录，产物 `labels.jsonl` / `labels.failed.jsonl` |
| `--model` | `Qwen/Qwen2.5-VL-7B-Instruct` | 显存够就换 32B，质量明显更好 |
| `--backend` | `transformers` | `transformers` 稳、`vllm` 快、`mock` 不加载模型 |
| `--batch-size` | 8 | 显存不够就调小（OOM 时降到 2~4） |
| `--max-new-tokens` | 400 | 摘要任务够用，调大没意义 |
| `--retries` | 1 | JSON 解析失败的条目再喂一次的轮数 |
| `--limit` | 0 | 只处理前 N 条（调试用） |
| `--shard` | `0/1` | 多卡分片，如 `2/4` |

---

## 时间与成本预估（单卡 H100）

| 配置 | 速度（估算） | 13,879 条耗时 |
|---|---|---|
| 7B + transformers | 8~15 条/秒 | **20~35 分钟** |
| 7B + vLLM | 25~40 条/秒 | **6~10 分钟** |
| 32B + transformers | 1~2 条/秒 | **2~4 小时** |
| 32B + vLLM | 3~5 条/秒 | **45~75 分钟** |

> 以上是估算，实际看日志里的 ETA。成本就是机器机时，没有 API 费用。
> 单卡 80GB 跑 32B 时 batch 必须压到 2，否则 KV cache 放不下。

---

## 常见问题

**模型下载慢 / 连不上 HuggingFace**
```bash
export HF_ENDPOINT=https://hf-mirror.com
# 或重跑 setup_h100.sh --hf-mirror 写入 ~/.bashrc
```

**CUDA out of memory**
- `--batch-size` 降到 4 或 2
- 换小模型（`Qwen/Qwen2.5-VL-3B-Instruct`）
- 多卡时用 `--shard` 而不是让一个进程吃所有卡

**失败率很高（`labels.failed.jsonl` 很长）**
- 先看 `raw` 字段，判断是模型不会输出 JSON，还是输出被截断
- 截断的话调大 `--max-new-tokens`
- 换 32B 模型通常能显著降低失败率

**`AutoModelForImageTextToText` 报错 / 不认这个模型**
- 模型太新，transformers 版本不够：重跑 `setup_h100.sh` 装最新版
- 或退回 `Qwen/Qwen2.5-VL-7B-Instruct`（兼容性最好）

**以后有多卡想加速**
- 一个进程只用一个模型实例。多卡时用 `--shard i/N` 开 N 个进程分别吃不同卡，跑完用 `merge_shards.sh` 合并（单卡用不到）

---

## 质量验收标准

跑完先别急着全量，抽 20 条人工看：

- [ ] 摘要能说清「谁对谁做了什么」，而不是「两个角色在互动」
- [ ] 汤姆 / 杰瑞 / 斯派克 不认错（这是硬伤，认错就没法检索）
- [ ] 道具识别正确率 > 70%
- [ ] JSON 失败率 < 3%

达不到就先换 32B 再试。**一次全量 30 分钟，返工也就半小时，别在质量上妥协。**
