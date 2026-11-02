#!/usr/bin/env bash
# 便捷入口：调用 fetch_tj.py（脚本只用 Python 标准库，不需要装依赖）
#
#   bash scripts/fetch_tj.sh --list
#   bash scripts/fetch_tj.sh                 # 全量下载，默认存到 external/resources/tj
#   bash scripts/fetch_tj.sh --workers 6
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="${PYTHON:-python3}"

command -v "$PY" >/dev/null 2>&1 || {
  echo "[错误] 找不到 $PY"
  exit 1
}

exec "$PY" "$HERE/fetch_tj.py" "$@"
