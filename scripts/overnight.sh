#!/usr/bin/env bash
# =============================================================================
#  无人值守 overnight 任务
#
#  流程：
#      1. 反复重试下载，直到 240 个文件补齐（或到截止时间）
#      2. 把不完整文件挪到 _partial/，避免污染流水线
#      3. 体检 → 剧集目录 → 镜头切分 → 后处理 → 抽帧 → 候选集
#      4. 导出元数据快照 + 提交
#      5. 尝试推送（没有 SSH key 会失败，只记录不中断）
#      6. 写 OVERNIGHT-REPORT.md
#      7. 关机
#
#  环境变量：
#      OVERNIGHT_DEADLINE_HOURS=5   下载最多等几小时（默认 5）
#      OVERNIGHT_NO_SHUTDOWN=1      不关机（调试用）
#      OVERNIGHT_DRY=1              跳过下载和流水线，只走汇报/提交（调试用）
#
#  日志：~/videolearning-logs/overnight-<时间>.log
#  报告：仓库根目录 OVERNIGHT-REPORT.md（会一起提交）
# =============================================================================
set -uo pipefail

REPO=/mnt/e/Repos/VideoLearning
VIDEO="$REPO/external/resources/tj"
PY=/home/stylinux2404/tj-gif/.venv/bin/python
REPORT="$REPO/OVERNIGHT-REPORT.md"

DOWNLOAD_DEADLINE_HOURS="${OVERNIGHT_DEADLINE_HOURS:-5}"
NO_SHUTDOWN="${OVERNIGHT_NO_SHUTDOWN:-0}"
DRY="${OVERNIGHT_DRY:-0}"

export PYTHONPATH="$REPO/src"
export TJGIF_DATA="${TJGIF_DATA:-$HOME/videolearning-data}"
export TJGIF_VIDEO="$VIDEO"
export TJGIF_AUDIT="$VIDEO/_audit.csv"

LOGDIR="$HOME/videolearning-logs"
mkdir -p "$LOGDIR"
LOG="$LOGDIR/overnight-$(date +%Y%m%d-%H%M).log"
: > "$LOG"

log() { echo "[$(date '+%F %T')] $*" | tee -a "$LOG"; }

START_TS=$(date +%s)
DOWNLOAD_STATE="未执行"
QUARANTINED=0
AUDIT_STATE="未执行"
SHOTS=0
CANDS=0
COMMIT_MSG=""
PUSH_STATE="未执行"

# ---------------------------------------------------------------- 下载

ensure_download() {
  log "== 阶段 1/5：补齐下载 =="
  if [ "$DRY" = "1" ]; then
    log "DRY 模式，跳过下载"
    DOWNLOAD_STATE="DRY 跳过"
    return
  fi

  local deadline=$(( $(date +%s) + DOWNLOAD_DEADLINE_HOURS * 3600 ))
  local round=0
  while :; do
    round=$((round + 1))
    local status
    status=$(cd "$REPO" && timeout 120 python3 scripts/fetch_tj.py --status 2>/dev/null | tail -1)
    [ -z "$status" ] && status="状态读取失败"
    DOWNLOAD_STATE="$status"
    log "第 $round 轮，当前: $status"

    case "$status" in
      *"partial=0"*"missing=0"*)
        log "全部文件已完整，结束下载"
        break
        ;;
    esac

    if [ "$(date +%s)" -ge "$deadline" ]; then
      log "到达下载截止时间（${DOWNLOAD_DEADLINE_HOURS} 小时），停止重试"
      break
    fi

    log "开始一轮下载（最多 40 分钟）"
    ( cd "$REPO" && timeout 2400 python3 -u scripts/fetch_tj.py --workers 6 ) >> "$LOG" 2>&1
    log "本轮退出码 $?，休息 30 秒"
    sleep 30
  done
}

quarantine_partial() {
  log "== 把不完整文件挪到 _partial/ =="
  if [ "$DRY" = "1" ]; then
    log "DRY 模式，跳过"
    return
  fi

  QUARANTINED=$("$PY" - <<'PY'
import shutil
import sys
from pathlib import Path

sys.path.insert(0, "/mnt/e/Repos/VideoLearning/scripts")
import fetch_tj as F

dest = Path("/mnt/e/Repos/VideoLearning/external/resources/tj")
pdir = dest / "_partial"
meta = F.fetch_metadata()
files, _ = F.select_originals(meta)

moved = 0
for f in files:
    target = dest / f["name"]
    if not target.exists():
        continue
    if f["size"] and target.stat().st_size != f["size"]:
        pdir.mkdir(parents=True, exist_ok=True)
        shutil.move(str(target), str(pdir / f["name"]))
        moved += 1
print(moved)
PY
)
  log "隔离不完整文件: $QUARANTINED 个"
}

# ---------------------------------------------------------------- 流水线

run_pipeline() {
  log "== 阶段 2/5：片源体检 =="
  if [ "$DRY" = "1" ]; then
    AUDIT_STATE="DRY 跳过"
    return
  fi

  rm -f "$TJGIF_AUDIT"
  if ( cd "$REPO" && timeout 1800 "$PY" scripts/audit_tj.py ) >> "$LOG" 2>&1; then
    AUDIT_STATE="成功"
    log "体检完成"
  else
    AUDIT_STATE="失败（见日志）"
    log "体检失败，后续步骤可能无法进行"
    return
  fi

  log "== 阶段 3/5：剧集目录 =="
  ( cd "$REPO" && timeout 300 "$PY" -m tjgif catalog ) >> "$LOG" 2>&1 \
    && log "catalog 完成" || log "catalog 失败"

  log "== 阶段 4/5：镜头切分（240 集约 20~30 分钟）=="
  ( cd "$REPO" && timeout 7200 "$PY" scripts/detect_shots.py --all ) >> "$LOG" 2>&1 \
    && log "detect_shots 完成" || log "detect_shots 失败"

  ( cd "$REPO" && timeout 600 "$PY" -m tjgif shots ) >> "$LOG" 2>&1 \
    && log "shots 完成" || log "shots 失败"

  SHOTS=$(wc -l < "$TJGIF_DATA/shots.jsonl" 2>/dev/null || echo 0)

  log "== 阶段 5/5：抽关键帧（约 40~60 分钟）=="
  ( cd "$REPO" && timeout 14400 "$PY" -m tjgif keyframes --workers 12 ) >> "$LOG" 2>&1 \
    && log "keyframes 完成" || log "keyframes 失败"

  ( cd "$REPO" && timeout 600 "$PY" -m tjgif candidates ) >> "$LOG" 2>&1 \
    && log "candidates 完成" || log "candidates 失败"

  CANDS=$(wc -l < "$TJGIF_DATA/candidates.jsonl" 2>/dev/null || echo 0)
}

# ---------------------------------------------------------------- 提交

commit_all() {
  log "== 导出元数据 + 提交 =="
  if [ "$DRY" != "1" ]; then
    ( cd "$REPO" && bash scripts/export_artifacts.sh ) >> "$LOG" 2>&1 \
      && log "元数据已导出" || log "导出失败"
  fi

  cd "$REPO" || return
  git add -A >> "$LOG" 2>&1
  COMMIT_MSG="overnight run $(date +%Y%m%d-%H%M)"
  if git diff --cached --quiet; then
    log "没有变更可提交"
    COMMIT_MSG="（无变更）"
  else
    git commit -q -m "$COMMIT_MSG" >> "$LOG" 2>&1 && log "已提交: $(git log --oneline -1)"
  fi

  log "== 尝试推送 =="
  local out
  out=$(GIT_TERMINAL_PROMPT=0 timeout 90 git push -u origin main 2>&1 | tail -3)
  if [ -z "$out" ]; then
    PUSH_STATE="推送成功（无输出）"
  else
    PUSH_STATE="$out"
  fi
  log "推送结果: $PUSH_STATE"
}

# ---------------------------------------------------------------- 汇报

write_report() {
  local elapsed=$(( ($(date +%s) - START_TS) / 60 ))
  local complete_count=0
  complete_count=$(ls "$VIDEO"/*.mp4 2>/dev/null | wc -l)

  {
    echo "# Overnight 运行报告"
    echo
    echo "生成时间：$(date '+%Y-%m-%d %H:%M:%S')"
    echo "总耗时：${elapsed} 分钟"
    echo
    echo "## 下载"
    echo
    echo '```'
    echo "$DOWNLOAD_STATE"
    echo '```'
    echo
    echo "- 视频目录现有 mp4：**${complete_count}** 个（目标 240）"
    echo "- 隔离的不完整文件：${QUARANTINED} 个（在 \`external/resources/tj/_partial/\`）"
    echo
    echo "## 流水线"
    echo
    echo "| 阶段 | 结果 |"
    echo "|---|---|"
    echo "| 片源体检 | ${AUDIT_STATE} |"
    echo "| 镜头数 | ${SHOTS} |"
    echo "| 候选镜头 | ${CANDS} |"
    echo
    echo "## 提交与推送"
    echo
    echo "- 提交信息：\`${COMMIT_MSG}\`"
    echo "- 推送结果："
    echo
    echo '```'
    echo "$PUSH_STATE"
    echo '```'
    echo
    echo "## 详细日志"
    echo
    echo "\`${LOG}\`"
    echo
    echo "## 醒来看这里"
    echo
    echo "1. 如果推送失败：把 SSH 公钥加到 GitHub 后执行 \`cd /mnt/e/Repos/VideoLearning && git push -u origin main\`"
    echo "2. 如果下载没补齐：\`bash scripts/fetch_tj.sh --workers 6\`（会续传）"
    echo "3. 后续打标：见 \`remote/README.md\`"
  } > "$REPORT"

  cd "$REPO" || return
  git add "$REPORT" >> "$LOG" 2>&1
  if ! git diff --cached --quiet; then
    git commit -q -m "add overnight report" >> "$LOG" 2>&1
  fi
  log "报告已写入: $REPORT"
}

# ---------------------------------------------------------------- 关机

shutdown_machine() {
  sync
  if [ "$NO_SHUTDOWN" = "1" ]; then
    log "OVERNIGHT_NO_SHUTDOWN=1，跳过关机"
    return
  fi
  log "60 秒后关机"
  if [ -x /mnt/c/Windows/System32/shutdown.exe ]; then
    /mnt/c/Windows/System32/shutdown.exe /s /t 60 /c "overnight job finished" >> "$LOG" 2>&1 \
      && log "关机命令已下发" || log "关机命令失败，请手动关机"
  else
    log "找不到 Windows shutdown.exe，请手动关机"
  fi
  SHUTDOWN_DONE=1
}

# ---------------------------------------------------------------- main

SHUTDOWN_DONE=0
# 兜底：脚本无论正常结束还是异常退出，都要关机
trap 'if [ "$SHUTDOWN_DONE" = "0" ]; then log "脚本退出，执行兜底关机"; shutdown_machine; fi' EXIT

log "======== 无人值守任务开始 ========"
log "仓库: $REPO"
log "数据: $TJGIF_DATA"
log "日志: $LOG"

ensure_download
quarantine_partial
run_pipeline
commit_all
write_report
shutdown_machine

log "======== 任务结束 ========"
