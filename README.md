# VideoLearning

视频语义检索与二次创作工具集。

当前项目：**《猫和老鼠》语义搜片 → 动图生成** —— 输入一句中文描述，从片源里检索语义最贴合的镜头，剪成循环 GIF。

---

## 目录结构

```
VideoLearning/
├── external/resources/tj/      片源（240 个 mp4，16GB，gitignore）
├── data/                       运行产物（gitignore）
│   └── derived/                小体积元数据快照（入库）
├── src/tjgif/                  流水线代码
├── scripts/
│   ├── fetch_tj.py/.sh         下载片源
│   ├── audit_tj.py             片源体检
│   ├── detect_shots.py         镜头切分（PySceneDetect）
│   ├── inspect_quality.py      抽帧质量核查
│   ├── setup.sh                环境初始化
│   └── export_artifacts.sh     导出元数据快照
├── remote/                     远程 GPU 打标包（拷到 H100 跑）
└── .gitignore
```

---

## 数据源

<https://archive.org/download/tom-and-jerry-cartoon-collection>

- **240 个原始 mp4**，约 **15.8 GB**
- 已排除 240 个 `.ia.mp4`（archive.org 自动转码的派生文件）
- 命名规整：`001   Puss Gets the Boot [1940].mp4`（编号 + 标题 + 年份）
- 每个文件可用 archive.org 元数据的 md5 校验

### 下载

```bash
bash scripts/fetch_tj.sh --list        # 看清单
bash scripts/fetch_tj.sh --limit 2     # 试 2 个
bash scripts/fetch_tj.sh --workers 8   # 全量（约 30~60 分钟）
```

实测：单连接只有 ~400 KB/s，**开 8 并发能到 6~11 MB/s**（archive.org 限单连接）。中断后重跑同一条命令即可续传。

### 体检

```bash
python scripts/audit_tj.py
```

输出分辨率/帧率/码率分布与低码率黑名单，明细写到 `external/resources/tj/_audit.csv`。
后续所有步骤都依赖这份 CSV（`tjgif catalog` 会读它）。

---

## 流水线

```bash
# 0. 环境（一次性）
bash scripts/setup.sh
source ~/video-learning-venv/bin/activate
# 复用已有环境： TJGIF_VENV=~/tj-gif/.venv bash scripts/setup.sh

export PYTHONPATH=$PWD/src   # 若没用 pip -e 安装

# 1. 剧集目录（需要 audit CSV）
python -m tjgif catalog

# 2. 镜头切分（240 集约 20~30 分钟）
python scripts/detect_shots.py --all

# 3. 镜头后处理（合并 <0.8s 碎片、切分 >8s 长镜头）
python -m tjgif shots

# 4. 抽关键帧 + 故事板 + 画面质量度量
python -m tjgif keyframes --workers 12

# 5. 过滤出可打标的候选镜头
python -m tjgif candidates

# 6. 导出元数据快照（进 git）
bash scripts/export_artifacts.sh
```

### 打标（远程 GPU）

见 [remote/README.md](remote/README.md)。打包命令：

```bash
bash remote/pack_inputs.sh
```

---

## 关键设计

| 决策 | 原因 |
|---|---|
| PySceneDetect 而非 ffmpeg `scene` | ffmpeg 只比帧差，动画上漏检 2~3 倍（旧源实测 19 vs 56 个切点） |
| 自适应正片边界 | 每集开头是 MGM 片头 + 标题卡，但长度每集不同（20~45s），写死会误伤 |
| 亮度单指标判正片 | 正片里也有几乎不动的镜头，用运动量会把起点推到 70 秒之后 |
| 4 帧拼故事板送 VLM | 单帧看不出「追」「摔」，四帧连起来才看得出动作 |
| 图片产物不入库 | 关键帧/故事板共 2GB+，但可由片源重建；切点/候选集/标签只有几 MB，入库 |

---

## 说明

本仓库只保存代码与文档。片源、关键帧、故事板、GIF 都在 `.gitignore` 里，
通过脚本获取或重建。
