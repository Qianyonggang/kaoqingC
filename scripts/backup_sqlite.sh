#!/usr/bin/env bash
set -euo pipefail

# 每日 SQLite 备份脚本（默认用于 /opt/kaoqingC 部署）
# 可通过环境变量覆盖：
#   APP_DIR=/opt/kaoqingC
#   DB_FILE=/opt/kaoqingC/data/attendance.db
#   BACKUP_DIR=/opt/kaoqingC/backup
#   RETENTION_DAYS=14
APP_DIR="${APP_DIR:-/opt/kaoqingC}"
DB_FILE="${DB_FILE:-$APP_DIR/data/attendance.db}"
BACKUP_DIR="${BACKUP_DIR:-$APP_DIR/backup}"
RETENTION_DAYS="${RETENTION_DAYS:-14}"

if [[ ! -f "$DB_FILE" ]]; then
  echo "错误：数据库文件不存在：$DB_FILE" >&2
  exit 1
fi

mkdir -p "$BACKUP_DIR"
BACKUP_FILE="$BACKUP_DIR/attendance_$(date +%F_%H-%M-%S).db"
cp "$DB_FILE" "$BACKUP_FILE"

# 删除超过保留天数的旧备份
find "$BACKUP_DIR" -type f -name 'attendance_*.db' -mtime +"$RETENTION_DAYS" -delete

echo "备份完成：$BACKUP_FILE"
