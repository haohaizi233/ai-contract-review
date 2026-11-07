import csv
from collections import defaultdict

counts = defaultdict(int)
cat_map = {}
total = 0

with open(
    "data/contract_merged/contracts_classification.csv", "r", encoding="utf-8-sig"
) as f:
    reader = csv.reader(f)
    header = next(reader)
    p_idx = header.index("业务分类代字")
    c_idx = header.index("业务分类名称")
    for row in reader:
        p = row[p_idx]
        c = row[c_idx]
        counts[p] += 1
        cat_map[p] = c
        total += 1

sorted_prefixes = sorted(counts.items(), key=lambda x: x[1], reverse=True)

with open("data/contract_merged/prefix_summary.txt", "w", encoding="utf-8") as f:
    f.write(f"Total Cases: {total}\n")
    f.write(f"Unique Prefixes: {len(sorted_prefixes)}\n\n")
    f.write("序号\t前缀代码\t业务分类释义\t案卷数量\t占比\n")
    for idx, (p, cnt) in enumerate(sorted_prefixes, 1):
        pct = (cnt / total) * 100
        c = cat_map.get(p, "未知")
        f.write(f"{idx}\t{p}\t{c}\t{cnt}\t{pct:.2f}%\n")

print(f"Exported {len(sorted_prefixes)} prefixes successfully.")
