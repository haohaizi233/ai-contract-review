# -*- coding: utf-8 -*-
import sqlite3
from pathlib import Path

db_path = r"C:\Users\Administrator\AppData\Roaming\WXDrive\sqlite3\uin13102671371216031.sqlite"
conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
cur = conn.cursor()

space_id = "s.1970324998235347.648114738G1H"
local_root = Path(
    r"D:\Users\Administrator\Documents\WXWork\1688855905806482\WeDrive\恒程创新\法律事务部（含商务合同等文件）"
)

cur.execute(
    """
    SELECT file_id, name, size, file_type, relativePath
    FROM File
    WHERE space_id=?
    ORDER BY file_type, relativePath
""",
    (space_id,),
)
rows = cur.fetchall()

cloud_dirs = []
cloud_files = []
local_found = []
local_missing = []

for fid, name, size, ftype, rel_path in rows:
    p = local_root / rel_path
    exists = p.exists()
    item_info = {
        "file_id": fid,
        "name": name,
        "size": int(size),
        "file_type": ftype,
        "rel_path": rel_path,
        "local_exists": exists,
    }
    if ftype == 1:
        cloud_dirs.append(item_info)
    else:
        cloud_files.append(item_info)
        if exists:
            local_found.append(item_info)
        else:
            local_missing.append(item_info)

print("=== 云端索引统计 (来自 WXDrive 本地SQLite缓存) ===")
print(f"云端目录数: {len(cloud_dirs)}")
print(f"云端文件/在线文档数: {len(cloud_files)}")
print(f"本地实际存在的文件: {len(local_found)}")
print(f"本地尚未下载/仅云端占位的文件: {len(local_missing)}")

print("\n--- 本地已存在文件 ---")
for f in local_found:
    print(f"  [FOUND] {f['name']} ({f['size']} bytes) -> {f['rel_path']}")

print("\n--- 待下载/云端占位文件示例 (前 15 个) ---")
for f in local_missing[:15]:
    print(
        f"  [MISSING] {f['name']} ({f['size']} bytes, type={f['file_type']}) -> {f['rel_path']}"
    )
