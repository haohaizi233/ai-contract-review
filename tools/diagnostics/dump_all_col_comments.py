import sys
from pathlib import Path

sys.path.insert(0, str(Path("tools").resolve()))
from tender_fullsync import load_env
import pymysql

env = load_env()
conn = pymysql.connect(
    host=env["MYSQL_HOST"],
    port=int(env["MYSQL_PORT"]),
    user=env["MYSQL_USER"],
    password=env["MYSQL_PWD"],
    database="tender_contract",
    charset="utf8mb4",
    cursorclass=pymysql.cursors.DictCursor,
)

with conn.cursor() as cur:
    cur.execute("""
        SELECT COLUMN_NAME, DATA_TYPE, COLUMN_COMMENT
        FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA = 'tender_contract'
          AND TABLE_NAME = 'contract_apply'
        ORDER BY ORDINAL_POSITION
    """)
    rows = cur.fetchall()

with open(
    "data/contract_merged/contract_apply_all_comments.txt", "w", encoding="utf-8"
) as f:
    for r in rows:
        if r["COLUMN_COMMENT"]:
            f.write(
                f"{r['COLUMN_NAME']:30s} | {r['DATA_TYPE']:10s} | {r['COLUMN_COMMENT']}\n"
            )

print("Dumped all comments.")
