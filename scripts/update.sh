#!/usr/bin/env bash
set -euo pipefail

# =========================
# 企业考勤系统一键更新脚本
# 功能：备份数据库 -> 更新代码 -> 安装依赖 -> 语法检查 -> 重启服务 -> 健康检查
# =========================

APP_DIR="/opt/kaoqingC"
VENV_PY="$APP_DIR/.venv/bin/python"
VENV_PIP="$APP_DIR/.venv/bin/pip"
SERVICE_NAME="kaoqing"
DB_FILE="$APP_DIR/data/attendance.db"
BACKUP_DIR="$APP_DIR/backup"
DEFAULT_BRANCH="work"
BRANCH="${1:-$DEFAULT_BRANCH}"

log() {
  echo "[$(date '+%F %T')] $*"
}

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "错误：缺少命令 $1，请先安装。" >&2
    exit 1
  }
}

main() {
  require_cmd systemctl
  require_cmd curl

  if [[ ! -d "$APP_DIR" ]]; then
    echo "错误：项目目录不存在：$APP_DIR" >&2
    exit 1
  fi

  cd "$APP_DIR"

  if [[ ! -x "$VENV_PY" ]]; then
    echo "错误：虚拟环境 Python 不存在或不可执行：$VENV_PY" >&2
    echo "请先创建虚拟环境并安装依赖。" >&2
    exit 1
  fi

  log "1/7 备份数据库"
  mkdir -p "$BACKUP_DIR"
  if [[ -f "$DB_FILE" ]]; then
    cp "$DB_FILE" "$BACKUP_DIR/attendance_$(date +%F_%H-%M-%S).db"
    log "数据库备份完成：$BACKUP_DIR"
  else
    log "未找到数据库文件（首次部署可忽略）：$DB_FILE"
  fi

  log "2/7 更新代码"
  if [[ -d .git ]]; then
    require_cmd git
    git fetch --all --prune
    git checkout "$BRANCH"
    git pull --ff-only
    log "Git 更新完成，当前分支：$(git branch --show-current)"
  else
    log "当前不是 Git 仓库，跳过拉取代码（请确保你已手动上传新代码）。"
  fi

  log "3/7 安装/同步依赖"
  "$VENV_PIP" install -r requirements.txt

  log "4/7 语法检查"
  "$VENV_PY" -m compileall app.py templates

  log "5/7 重启应用服务"
  systemctl restart "$SERVICE_NAME"

  log "6/7 服务状态与日志"
  systemctl --no-pager -l status "$SERVICE_NAME"
  journalctl -u "$SERVICE_NAME" -n 50 --no-pager

  log "7/7 健康检查"
  curl -I --max-time 8 http://127.0.0.1:5000
  curl -I --max-time 8 http://127.0.0.1

  log "更新完成。"
  log "如果你使用域名，请再执行：dig +short 你的域名"
}

main "$@"
