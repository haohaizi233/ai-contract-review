import csv
from collections import defaultdict

data = defaultdict(list)
with open('data/contract_merged/contracts_classification.csv', 'r', encoding='utf-8-sig') as f:
    reader = csv.reader(f)
    header = next(reader)
    p_idx = header.index('业务分类代字')
    c_idx = header.index('业务分类名称')
    name_idx = header.index('合同名称')
    opp_idx = header.index('对方主体')
    app_idx = header.index('申请人')
    no_idx = header.index('合同编号')
    
    for row in reader:
        p = row[p_idx]
        data[p].append({
            'no': row[no_idx],
            'name': row[name_idx],
            'opp': row[opp_idx],
            'app': row[app_idx]
        })

with open('data/contract_merged/prefix_samples.txt', 'w', encoding='utf-8') as f:
    for p, items in sorted(data.items(), key=lambda x: len(x[1]), reverse=True):
        f.write(f"=== 前缀: {p} (共 {len(items)} 案) ===\n")
        for it in items[:3]:
            f.write(f"  编号: {it['no']} | 名称: {it['name']} | 对方: {it['opp']} | 申请人: {it['app']}\n")
        f.write("\n")

print("Samples written to prefix_samples.txt")
