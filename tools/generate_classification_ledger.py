import csv
import json
import re
import time
from pathlib import Path

root = Path(".")
base = root / "data" / "contract_merged"
cases_dir = base / "cases"
jsonl_file = base / "contracts_merged.jsonl"
out_csv = base / "contracts_classification.csv"

PREFIX_MEANINGS = {
    "CIPCX": "撤销复审与无效争议业务",
    "CIP": "专利/商标代理核心业务",
    "HCCX": "华诚总部综合合同业务",
    "CIPSJ": "外观设计知产代理业务",
    "HCI": "涉外国际业务流程",
    "GW": "常年/专项知识产权顾问",
    "MG": "知产托管与年费维护业务",
    "SJI": "涉外外观设计业务",
    "_无编号申请": "无编号立项申请单据",
    "HCHZS": "杭州分部直属业务",
    "TZ": "台州分部业务",
    "CIPBJ": "北京分部知产业务",
    "HCH": "华诚杭州业务",
    "HKP": "香港/涉外专利业务",
    "HCBM": "涉外/保密业务协议",
    "SJZ": "设计综合业务",
    "JNHC": "济南分部业务",
    "YCGD": "宜昌/广东分部业务",
    "HCG": "华诚顾问/高级业务",
    "HCZ": "华诚综合管理合同",
    "BQ": "版权/软件著作权登记业务",
    "BJH": "北京分部综合业务",
    "BJI": "北京分部涉外业务",
    "BJP": "北京分部专利业务",
}


def get_prefix(k):
    if k.startswith("UNNUMBERED"):
        return "_无编号申请"
    m = re.match(r"^([A-Za-z]+)", k)
    if m:
        return m.group(1).upper()
    m2 = re.match(r"^(\d{2,})", k)
    if m2:
        return "数字开头单号"
    return "_其他业务"


def get_year(k, text):
    m = re.search(r"20(2[4-6])", k)
    if m:
        return "20" + m.group(1)
    m2 = re.search(r"20(2[4-6])", text[:500])
    if m2:
        return "20" + m2.group(1)
    return "2024~2026"


print(f"开始生成全量案件多维度分类索引表 -> {out_csv} ...")
t0 = time.time()

records = [
    json.loads(line_str)
    for line_str in jsonl_file.read_text(encoding="utf-8").splitlines()
]
total = len(records)
rows = []

# Quick regexes for metadata extraction from case MD header
re_name = re.compile(r"- 合同名称：(.*)")
re_status = re.compile(r"\|\s*合同状态\s*\|\s*([^|]+)\|")
re_applicant = re.compile(r"\|\s*申请人\s*\|\s*([^|]+)\|")
re_mine = re.compile(r"\|\s*我方主体\s*\|\s*([^|]+)\|")
re_other = re.compile(r"\|\s*他方主体\s*\|\s*([^|]+)\|")

for idx, r in enumerate(records, 1):
    k = r["分组键"]
    pfx = get_prefix(k)
    meaning = PREFIX_MEANINGS.get(pfx, "区域分部/专项其他业务")

    p = base / r["文件"]
    try:
        text = p.read_text(encoding="utf-8")
    except Exception:
        text = ""

    yr = get_year(k, text)

    # Contract name
    m_n = re_name.search(text)
    c_name = m_n.group(1).strip() if m_n else r.get("合同编号", "")

    # Status
    m_s = re_status.search(text)
    status = m_s.group(1).strip() if m_s else "流转中/归档"

    # Applicant
    m_a = re_applicant.search(text)
    applicant = m_a.group(1).strip() if m_a else ""

    # Parties
    m_m = re_mine.search(text)
    mine = m_m.group(1).strip() if m_m else ""
    if mine in ("–", "—", "-"):
        mine = ""

    m_o = re_other.search(text)
    other = m_o.group(1).strip() if m_o else ""
    if other in ("–", "—", "-"):
        other = ""

    # Count attachments and images in MD
    att_cnt = text.count("../attachments/")
    alb_cnt = text.count("../albums/")

    rows.append(
        {
            "合同编号": r.get("合同编号", "") or k,
            "分组键": k,
            "业务分类代字": pfx,
            "业务分类名称": meaning,
            "年份": yr,
            "两库覆盖": r.get("覆盖类别", "tender_only"),
            "合同名称": c_name,
            "合同状态": status,
            "申请人": applicant,
            "我方签约主体": mine,
            "对方主体": other,
            "有效附件数": att_cnt,
            "相册图片数": alb_cnt,
            "缺口数": r.get("缺口数", 0),
            "案卷MD文件": r["文件"],
        }
    )

    if idx % 5000 == 0 or idx == total:
        print(
            f"  - 进度: [{idx:,} / {total:,}] ({(idx / total) * 100:.1f}%) ...",
            flush=True,
        )

fieldnames = [
    "合同编号",
    "分组键",
    "业务分类代字",
    "业务分类名称",
    "年份",
    "两库覆盖",
    "合同名称",
    "合同状态",
    "申请人",
    "我方签约主体",
    "对方主体",
    "有效附件数",
    "相册图片数",
    "缺口数",
    "案卷MD文件",
]

with out_csv.open("w", encoding="utf-8-sig", newline="") as fp:
    writer = csv.DictWriter(fp, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(rows)

t1 = time.time()
print(
    f"\n分类表生成成功！共写入 {len(rows):,} 案，耗时: {t1 - t0:.2f} 秒。文件大小: {out_csv.stat().st_size:,} 字节。"
)
