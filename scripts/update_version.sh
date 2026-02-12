#!/usr/bin/env bash
set -euo pipefail

# 一键更新脚本：备份数据库 -> 拉代码 -> 安装依赖 -> 语法检查 -> 重启服务 -> 健康检查
# 用法：
#   ./scripts/update_version.sh [branch] [repo_url]
# 例子：
#   ./scripts/update_version.sh work
#   ./scripts/update_version.sh main https://github.com/Qianyonggang/kaoqingC.git
# 备份模式：
#   BACKUP_MODE=rotate（默认，按时间戳保留历史）
#   BACKUP_MODE=overwrite（每天覆盖同一个文件 attendance_latest.db）
APP_DIR="/opt/kaoqingC"
VENV_PY="$APP_DIR/.venv/bin/python"
VENV_PIP="$APP_DIR/.venv/bin/pip"
SERVICE_NAME="kaoqing"
ENV_FILE="$APP_DIR/.env"
DB_FILE="$APP_DIR/data/attendance.db"
BACKUP_DIR="$APP_DIR/backup"
DEFAULT_BRANCH="main"
BRANCH="${1:-$DEFAULT_BRANCH}"
REPO_URL="${2:-${REPO_URL:-}}"
BACKUP_MODE="${BACKUP_MODE:-rotate}"
HEALTH_RETRIES="${HEALTH_RETRIES:-8}"
HEALTH_WAIT_SEC="${HEALTH_WAIT_SEC:-2}"
APP_HOST="127.0.0.1"
APP_PORT="5000"

log() {
  echo "[$(date '+%F %T')] $*"
}

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "错误：缺少命令 $1" >&2
    exit 1
  }
}

ensure_repo_ready() {
  need_cmd git

  if [[ -d .git ]]; then
    if [[ -n "$REPO_URL" ]]; then
      log "检测到你指定了仓库地址，更新 origin -> $REPO_URL"
      git remote set-url origin "$REPO_URL"
    fi
    return
  fi

  [[ -n "$REPO_URL" ]] || {
    echo "错误：当前目录不是 Git 仓库，且未提供 repo_url。" >&2
    echo "请执行：./scripts/update_version.sh <branch> <repo_url>" >&2
    exit 1
  }

  log "当前目录不是 Git 仓库，使用 repo_url 初始化：$REPO_URL"
  git init
  git remote add origin "$REPO_URL"
}

backup_db() {
  mkdir -p "$BACKUP_DIR"
  if [[ ! -f "$DB_FILE" ]]; then
    log "未找到数据库文件（首次部署可忽略）：$DB_FILE"
    return
  fi

  case "$BACKUP_MODE" in
    rotate)
      cp "$DB_FILE" "$BACKUP_DIR/attendance_$(date +%F_%H-%M-%S).db"
      log "数据库备份完成（历史保留）：$BACKUP_DIR"
      ;;
    overwrite)
      cp "$DB_FILE" "$BACKUP_DIR/attendance_latest.db"
      log "数据库备份完成（覆盖模式）：$BACKUP_DIR/attendance_latest.db"
      ;;
    *)
      echo "错误：BACKUP_MODE 只支持 rotate 或 overwrite，当前是：$BACKUP_MODE" >&2
      exit 1
      ;;
  esac
}

load_runtime_config() {
  if [[ -f "$ENV_FILE" ]]; then
    local env_port
    env_port="$(sed -n 's/^PORT=//p' "$ENV_FILE" | tail -n 1 | tr -d '[:space:]')"
    if [[ -n "$env_port" ]]; then
      APP_PORT="$env_port"
    fi
  fi
}

health_check_with_retry() {
  local i
  local app_ok=0

  for ((i = 1; i <= HEALTH_RETRIES; i++)); do
    if curl -fsS -I --max-time 8 "http://${APP_HOST}:${APP_PORT}" >/dev/null; then
      app_ok=1
      break
    fi
    log "应用健康检查第 ${i}/${HEALTH_RETRIES} 次失败，${HEALTH_WAIT_SEC}s 后重试..."
    sleep "$HEALTH_WAIT_SEC"
  done

  if [[ "$app_ok" -ne 1 ]]; then
    echo "错误：应用健康检查失败，无法连接 http://${APP_HOST}:${APP_PORT}" >&2
    echo "请检查：1) .env 中 PORT 是否与 systemd 启动参数一致；2) 服务是否启动成功。" >&2
    systemctl --no-pager -l status "$SERVICE_NAME" || true
    journalctl -u "$SERVICE_NAME" -n 120 --no-pager || true
    exit 1
  fi

  curl -I --max-time 8 "http://${APP_HOST}:${APP_PORT}"
  curl -I --max-time 8 http://127.0.0.1
}

main() {
  need_cmd systemctl
  need_cmd curl

  [[ -d "$APP_DIR" ]] || {
    echo "错误：项目目录不存在：$APP_DIR" >&2
    exit 1
  }

  cd "$APP_DIR"
  load_runtime_config

  [[ -x "$VENV_PY" ]] || {
    echo "错误：虚拟环境 Python 不可执行：$VENV_PY" >&2
    echo "请先完成部署流程创建 .venv。" >&2
    exit 1
  }

  log "1/7 备份数据库"
  backup_db

  log "2/7 更新代码"
  ensure_repo_ready
  git fetch --all --prune
  git checkout "$BRANCH"
  git pull --ff-only origin "$BRANCH"
  log "代码更新完成，当前分支：$(git branch --show-current)"

  log "3/7 安装依赖"
  "$VENV_PIP" install -r requirements.txt

  log "4/7 语法检查"
  "$VENV_PY" -m compileall app.py templates

  log "5/7 重启服务"
  systemctl restart "$SERVICE_NAME"

  log "6/7 查看服务状态"
  systemctl --no-pager -l status "$SERVICE_NAME"
  journalctl -u "$SERVICE_NAME" -n 60 --no-pager

  log "7/7 健康检查"
  health_check_with_retry

  log "更新完成。"
}

main "$@"
