import pytds

env = {}
for line in open("data/.db_env", encoding="utf-8"):
    line = line.strip()
    if line.startswith("export ") and "=" in line:
        k, v = line[len("export ") :].split("=", 1)
        env[k.strip()] = v.strip().strip("'\"")

conn = pytds.connect(
    dsn=env["DB_HOST"],
    port=int(env["DB_PORT"]),
    user=env["DB_USER"],
    password=env["DB_PWD_READONLY"],
    database="ecologyE9",
    timeout=30,
)
cur = conn.cursor()
cur.execute("""
SELECT b.id, b.workflowname, count(*)
FROM workflow_requestbase r
JOIN workflow_base b ON r.workflowid = b.id
WHERE b.workflowname LIKE '%合同%' OR b.workflowname LIKE '%委%' OR b.workflowname LIKE '%协议%'
GROUP BY b.id, b.workflowname
ORDER BY count(*) DESC
""")
print("=== e9 Contract/Agreement Workflows ===")
rows = cur.fetchall()
total_reqs = 0
for row in rows:
    total_reqs += row[2]
    print(f"ID {row[0]}: {row[1]} -> {row[2]} requests")
print(f"Total requests in these workflows: {total_reqs}")

# Check requestmark patterns
cur.execute("""
SELECT TOP 20 requestid, requestmark, requestname, createdate
FROM workflow_requestbase
WHERE workflowid IN (SELECT id FROM workflow_base WHERE workflowname LIKE '%合同%' OR workflowname LIKE '%委%' OR workflowname LIKE '%协议%')
  AND requestmark IS NOT NULL AND requestmark != ''
ORDER BY requestid DESC
""")
print("\n=== Recent 20 Requestmarks ===")
for r in cur.fetchall():
    print(r)

conn.close()
