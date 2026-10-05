#!/bin/sh
# 只清空面板自己的站点记录、数据库账本、操作日志和备份任务元数据。
# 不会删除 /etc/nginx、/var/www/panel、/www、jwt_secret、secret.key、admin.json，也不会删除备份压缩包。
set -eu

if [ "${CONFIRM_FACTORY_RESET:-}" != "yes" ] || [ "${1:-}" != "--i-understand" ]; then
  echo "拒绝执行：必须同时设置 CONFIRM_FACTORY_RESET=yes，并且第一个参数是 --i-understand"
  exit 1
fi

ROOT=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
STORE="$ROOT/backend/data/nginx/sites.json"
LEDGER="$ROOT/backend/data/mysql/panel_databases.sqlite"
LOGDB="$ROOT/backend/data/logs/panel.sqlite"
TASKS="$ROOT/backend/data/backup/tasks.json"
TASKDB="$ROOT/backend/data/backup/task_logs.sqlite"

blocked=$(python3 - "$STORE" << 'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
if not path.is_file():
    print("no")
    raise SystemExit(0)
text = path.read_text(encoding="utf-8")
try:
    data = json.loads(text)
except json.JSONDecodeError:
    print("yes")
    raise SystemExit(0)
blocked = "panel-proxy zhuanquan" in text
if isinstance(data, dict) and "20.187.70.213" in data:
    blocked = True
print("yes" if blocked else "no")
PY
) || { echo "错误原因：无法读取站点数据"; exit 1; }

if [ "$blocked" = "yes" ] && [ "${ALLOW_ON_PRODUCTION_HOST:-}" != "yes" ]; then
  echo "拒绝执行：站点库包含生产站点 20.187.70.213 或标记 panel-proxy zhuanquan，未设置 ALLOW_ON_PRODUCTION_HOST=yes"
  exit 1
fi

python3 - "$STORE" << 'PY' || { echo "错误原因：无法清空站点数据"; exit 1; }
import sys
from pathlib import Path

path = Path(sys.argv[1])
if path.is_symlink():
    raise SystemExit(1)
path.parent.mkdir(parents=True, exist_ok=True)
temporary = path.with_suffix(".json.tmp")
temporary.write_text("{}\n", encoding="utf-8")
temporary.replace(path)
PY

python3 - "$LEDGER" << 'PY' || { echo "错误原因：无法清空数据库账本"; exit 1; }
import sqlite3
import sys
from pathlib import Path

path = Path(sys.argv[1])
if not path.is_file() or path.is_symlink():
    raise SystemExit(0)
connection = sqlite3.connect(path)
try:
    names = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    if "databases" in names:
        connection.execute("DELETE FROM databases")
        connection.commit()
finally:
    connection.close()
PY

python3 - "$LOGDB" << 'PY' || { echo "错误原因：无法清空面板操作日志"; exit 1; }
import sqlite3
import sys
from pathlib import Path

path = Path(sys.argv[1])
if not path.is_file() or path.is_symlink():
    raise SystemExit(0)
connection = sqlite3.connect(path)
try:
    connection.execute("DELETE FROM panel_logs")
    connection.commit()
finally:
    connection.close()
PY

python3 - "$TASKS" << 'PY' || { echo "错误原因：无法清空备份任务元数据"; exit 1; }
import sys
from pathlib import Path

path = Path(sys.argv[1])
if path.is_symlink():
    raise SystemExit(1)
if not path.parent.is_dir():
    raise SystemExit(0)
temporary = path.with_suffix(".json.tmp")
temporary.write_text("[]\n", encoding="utf-8")
temporary.replace(path)
PY

python3 - "$TASKDB" << 'PY' || { echo "错误原因：无法清空备份任务记录"; exit 1; }
import sqlite3
import sys
from pathlib import Path

path = Path(sys.argv[1])
if not path.is_file() or path.is_symlink():
    raise SystemExit(0)
connection = sqlite3.connect(path)
try:
    connection.execute("DELETE FROM task_logs")
    connection.commit()
finally:
    connection.close()
PY

echo "面板站点记录、数据库账本、操作日志和备份任务元数据已清空"
