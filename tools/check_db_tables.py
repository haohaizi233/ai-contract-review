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
    cur.execute("SHOW TABLES")
    tables = [list(r.values())[0] for r in cur.fetchall()]

dict_tables = [
    t
    for t in tables
    if any(
        k in t.lower()
        for k in ["dict", "type", "config", "item", "code", "category", "base"]
    )
]

with open("data/contract_merged/db_tables.txt", "w", encoding="utf-8") as f:
    f.write("All tables in tender_contract:\n")
    for t in tables:
        f.write(f"  {t}\n")
    f.write("\nPotential dictionary/mapping tables:\n")
    for t in dict_tables:
        f.write(f"  {t}\n")

print("Dumped tables info.")
