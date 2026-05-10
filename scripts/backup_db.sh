#!/bin/bash
# 数据库自动备份脚本
# 部署到服务器后加 cron（每天凌晨 2:00）：
#   echo "0 2 * * * root /opt/content-pipeline/scripts/backup_db.sh >> /opt/content-pipeline/logs/backup.log 2>&1" > /etc/cron.d/medai-backup

set -euo pipefail

DB=/opt/content-pipeline/corpus/corpus.db
BACKUP_DIR=/opt/content-pipeline/backups
LOG_DIR=/opt/content-pipeline/logs
DATE=$(date +%Y%m%d_%H%M)
KEEP_DAYS=7

mkdir -p "$BACKUP_DIR" "$LOG_DIR"

if [ ! -f "$DB" ]; then
    echo "[$(date)] ❌ 数据库文件不存在: $DB"
    exit 1
fi

# WAL checkpoint：把 WAL 日志合并回主库，保证备份文件完整
python3 -c "
import sqlite3
conn = sqlite3.connect('$DB')
conn.execute('PRAGMA wal_checkpoint(TRUNCATE)')
conn.close()
print('[$(date)] WAL checkpoint 完成')
"

# 复制数据库
cp "$DB" "$BACKUP_DIR/corpus_${DATE}.db"
SIZE=$(du -sh "$BACKUP_DIR/corpus_${DATE}.db" | cut -f1)
echo "[$(date)] ✅ 备份完成: corpus_${DATE}.db (${SIZE})"

# 清理超过 KEEP_DAYS 天的旧备份
DELETED=$(find "$BACKUP_DIR" -name "corpus_*.db" -mtime +${KEEP_DAYS} -print -delete | wc -l | tr -d ' ')
[ "$DELETED" -gt 0 ] && echo "[$(date)] 🗑  清理旧备份: ${DELETED} 个"

# 列出当前备份
echo "[$(date)] 当前备份列表:"
ls -lh "$BACKUP_DIR"/corpus_*.db 2>/dev/null | awk '{print "  " $NF, $5}'
