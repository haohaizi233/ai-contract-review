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
        SELECT id, template_code, template_name, contract_type, business_type, auth_item, version_no
        FROM contract_template
        ORDER BY id
    """)
    rows = cur.fetchall()

with open("data/contract_merged/db_templates_all.txt", "w", encoding="utf-8") as f:
    f.write(f"Total templates in contract_template table: {len(rows)}\n\n")
    for r in rows:
        f.write(
            f"{r['template_code']} | {r['template_name']} | contract_type={r['contract_type']} | business_type={r['business_type']} | auth_item={r['auth_item']} | {r['version_no']}\n"
        )

print(f"Dumped {len(rows)} templates from DB.")
