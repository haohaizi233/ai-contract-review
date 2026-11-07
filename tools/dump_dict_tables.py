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
    with open("data/contract_merged/db_dict_contents.txt", "w", encoding="utf-8") as f:
        # 1. contract_business_type
        f.write("=== Table: contract_business_type ===\n")
        cur.execute("SELECT * FROM contract_business_type")
        for r in cur.fetchall():
            f.write(f"  {r}\n")
        f.write("\n")

        # 2. contract_type
        f.write("=== Table: contract_type ===\n")
        cur.execute("SELECT * FROM contract_type")
        for r in cur.fetchall():
            f.write(f"  {r}\n")
        f.write("\n")

        # 3. contract_config_matter
        f.write("=== Table: contract_config_matter ===\n")
        cur.execute("SELECT * FROM contract_config_matter LIMIT 10")
        for r in cur.fetchall():
            f.write(f"  {r}\n")
        f.write("\n")

        # 4. contract_config_matter_dict
        f.write("=== Table: contract_config_matter_dict ===\n")
        cur.execute("SELECT * FROM contract_config_matter_dict LIMIT 10")
        for r in cur.fetchall():
            f.write(f"  {r}\n")
        f.write("\n")

        # 5. contract_template
        f.write("=== Table: contract_template ===\n")
        cur.execute("SELECT * FROM contract_template LIMIT 5")
        for r in cur.fetchall():
            f.write(f"  {r}\n")
        f.write("\n")

print("Dumped dict contents.")
