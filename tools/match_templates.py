import os, glob, re
from collections import defaultdict

# 1. Load reference template names
ref_files = [f for f in os.listdir('参考') if f.endswith('.docx')]
template_keywords = {}
for f in ref_files:
    # e.g. 国内专利服务合同（专利侵权分析）-恒程创新代理有限公司.docx -> 专利侵权分析
    m = re.search(r'（(.*?)）', f)
    kw = m.group(1) if m else f.replace('.docx', '')
    template_keywords[f] = kw

print(f"Loaded {len(template_keywords)} reference templates.")
for k, v in list(template_keywords.items())[:10]:
    print(f"  {k} -> [{v}]")

# 2. Check sample cases to see how well they match
files = glob.glob('data/contract_merged/cases/*/*.md')
matches = defaultdict(int)
sample_evidence = defaultdict(list)

for f in files[:2000]:
    with open(f, 'r', encoding='utf-8') as fp:
        text = fp.read()
    
    # check if any template keyword or exact template name appears in:
    # a) attachment filename
    # b) contract_name
    # c) body text
    fname = os.path.basename(f)
    for t_file, kw in template_keywords.items():
        base_name = t_file.replace('.docx', '')
        # check attachment or contract name
        if base_name in text:
            matches[t_file] += 1
            if len(sample_evidence[t_file]) < 2:
                sample_evidence[t_file].append((fname, 'exact_template_name_in_md'))
        elif kw in text:
            # check if kw appears in contract_name or attachments
            for line in text.splitlines():
                if ('contract_name' in line or '需盖章文件' in line or '合同名称' in line) and kw in line:
                    matches[t_file] += 1
                    if len(sample_evidence[t_file]) < 2:
                        sample_evidence[t_file].append((fname, line.strip()[:100]))
                    break

print("\nTemplate Match Stats (sampled across 2,000 cases):")
for t_file, cnt in sorted(matches.items(), key=lambda x: x[1], reverse=True):
    print(f"  {cnt:4d} cases matched: {t_file}")
    for s_case, s_line in sample_evidence[t_file][:1]:
        print(f"       Ex: [{s_case}] -> {s_line}")
