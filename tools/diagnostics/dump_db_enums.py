import sys
from pathlib import Path

sys.path.insert(0, str(Path("tools").resolve()))
from tools.integrations.tender.tender_fullsync import load_env
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
          AND COLUMN_NAME IN ('draft_type', 'contract_type', 'business_type', 'contract_nature', 'contract_status', 'contract_category', 'auth_item')
    """)
    rows = cur.fetchall()

with open("data/contract_merged/db_enum_inspect.txt", "w", encoding="utf-8") as f:
    f.write("=== Column Comments in tender_contract.contract_apply ===\n")
    for r in rows:
        f.write(f"Column: {r['COLUMN_NAME']} ({r['DATA_TYPE']})\n")
        f.write(f"Comment: {r['COLUMN_COMMENT']}\n\n")

print("Dumped enum comments.")
