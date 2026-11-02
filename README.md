# VideoLearning

视频语义检索与二次创作工具集。

当前项目：**《猫和老鼠》语义搜片 → 动图生成**（输入一句中文描述，从片源里检索语义最贴合的镜头并剪成 GIF）。

---

## 目录结构

```
VideoLearning/
├── scripts/
│   ├── fetch_tj.py          从 archive.org 下载片源（只取原始 mp4）
│   └── fetch_tj.sh          便捷入口
├── external/
│   └── resources/
│       └── tj/              ★ 片源目录（16GB，已 gitignore）
└── .gitignore
```

---

## 片源

数据源：<https://archive.org/download/tom-and-jerry-cartoon-collection>

- **240 个原始 mp4**，约 **15.8 GB**
- 已排除 240 个 `.ia.mp4`（archive.org 自动转码的派生文件）
- 每个文件带 archive.org 提供的 md5，下载后自动校验

### 下载

```bash
# 先看看会下哪些
bash scripts/fetch_tj.sh --list

# 先下 2 个验证通路
bash scripts/fetch_tj.sh --limit 2

# 全量下载（断点续传，中断后重跑即可）
bash scripts/fetch_tj.sh --workers 4
```

默认存到 `external/resources/tj/`，可用 `--dest` 覆盖。

**特性**：

| 特性 | 说明 |
|---|---|
| 断点续传 | `curl -C -`，中断后重跑不重复下载 |
| 跳过已完成 | 已存在且大小一致的文件直接跳过 |
| md5 校验 | 校验失败自动删除并重下 |
| 并发 | 默认 4，archive.org 建议 ≤6 |
| 仅标准库 | 不需要 pip 安装任何东西 |

---

## 说明

本仓库只保存代码与文档。片源、关键帧、索引等体积大或可再生的内容都在 `.gitignore` 里，
通过上面的脚本获取。
