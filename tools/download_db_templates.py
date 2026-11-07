# -*- coding: utf-8 -*-
"""
Download all active standard contract templates (78 official templates, including the 52 missing from 参考/)
from the OSS URLs recorded in tender_contract.contract_attachment (source_type=3).
"""

import os
import re
import sys
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tender_fullsync import load_env
import pymysql


_ENTITY_SUFFIX = os.environ.get("CONTRACT_KB_TEMPLATE_ENTITY_SUFFIX", "标准代理主体")
_ENTITY_FULL = os.environ.get(
    "CONTRACT_KB_TEMPLATE_ENTITY_FULL", "标准知识产权代理主体"
)

REF_EXISTING_MAP = {
    "MB20251205012": f"国内专利服务合同（仅快速预审）-{_ENTITY_SUFFIX}.docx",
    "MB20251205013": f"国内专利服务合同（年费缴纳）-{_ENTITY_SUFFIX}.docx",
    "MB20251205014": f"国内专利服务合同（申请）-{_ENTITY_SUFFIX}.docx",
    "MB20251205015": f"国内专利服务合同（许可备案）-{_ENTITY_SUFFIX}.docx",
    "MB20251205016": f"国内专利服务合同（许可备案注销）-{_ENTITY_SUFFIX}.docx",
    "MB20251205017": f"国内专利服务合同（优先权等副本）-{_ENTITY_SUFFIX}.docx",
    "MB20251205018": "国内专利服务合同（仅优先审查）.docx",
    "MB20251205019": f"国内专利服务合同（著录项目变更）-{_ENTITY_SUFFIX}.docx",
    "MB20251205020": f"国内专利服务合同（专利导航分析报告）-{_ENTITY_SUFFIX}.docx",
    "MB20251205021": f"国内专利服务合同（专利复审）-{_ENTITY_SUFFIX}.docx",
    "MB20251205022": f"国内专利服务合同（专利检索报告）-{_ENTITY_SUFFIX}.docx",
    "MB20251205023": f"国内专利服务合同（专利尽职调查报告）-{_ENTITY_SUFFIX}.docx",
    "MB20251205024": f"国内专利服务合同（专利评价报告）-{_ENTITY_SUFFIX}.docx",
    "MB20251205025": f"国内专利服务合同（专利侵权分析）-{_ENTITY_SUFFIX}.docx",
    "MB20251205026": f"国内专利服务合同（专利稳定性分析报告）-{_ENTITY_SUFFIX} .docx",
    "MB20251205027": f"国内专利服务合同（转让）-{_ENTITY_SUFFIX}.docx",
    "MB20251205047": f"国内商标服务合同（变更）-{_ENTITY_SUFFIX}.docx",
    "MB20251205048": f"国内商标服务合同（驳回复审）-{_ENTITY_SUFFIX}.docx",
    "MB20251205051": f"国内商标服务合同（撤三）-{_ENTITY_SUFFIX}.docx",
    "MB20251205053": f"国内商标服务合同（许可备案）-{_ENTITY_SUFFIX}.docx",
    "MB20251205054": f"国内商标服务合同（续展）-{_ENTITY_SUFFIX}.docx",
    "MB20251205055": f"国内商标服务合同（异议）-{_ENTITY_SUFFIX}.docx",
    "MB20251205056": f"国内商标服务合同（异议答辩）-{_ENTITY_SUFFIX}.docx",
    "MB20251205057": f"国内商标服务合同（注册）-{_ENTITY_SUFFIX}.docx",
    "MB20251205058": f"国内商标服务合同（转让）-{_ENTITY_SUFFIX}.docx",
    "MB20251225003": f"（含国际业务报价版）知识产权服务合同-{_ENTITY_FULL}.docx",
}


def get_category_folder(business_type: int, auth_item: str) -> str:
    ai = (auth_item or "").strip()
    if business_type == 8:
        return "08_总委框架与保密协议"
    if business_type == 7 and ai != "7":
        return "08_总委框架与保密协议"
    if "," in ai:
        return "04_跨板块组合合同"
    if ai == "1":
        return "01_国内专利"
    if ai == "2":
        return "02_国内商标"
    if ai == "3":
        return "03_国内著作权"
    if ai in ("8", "9"):
        return "05_国际专利与国际商标"
    if ai in ("4", "5"):
        return "06_科创项目与财税审计"
    if ai == "7":
        return "07_法律服务与维权诉讼"
    return "08_总委框架与保密协议"


def safe_filename(s: str) -> str:
    return re.sub(r'[\\/:*?"<>|]', "_", s).strip()


def encode_url(raw_url: str) -> str:
    parsed = urllib.parse.urlsplit(raw_url)
    quoted_path = urllib.parse.quote(
        urllib.parse.unquote(parsed.path), safe="/()（）-_.~"
    )
    return urllib.parse.urlunsplit(
        (parsed.scheme, parsed.netloc, quoted_path, parsed.query, parsed.fragment)
    )


def main():
    env = load_env()
    conn = pymysql.connect(
        host=env["MYSQL_HOST"],
        port=int(env.get("MYSQL_PORT", 3306)),
        user=env["MYSQL_USER"],
        password=env["MYSQL_PWD"],
        database="tender_contract",
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
    )

    with conn.cursor() as cur:
        cur.execute("""
            SELECT t.id, t.template_code, t.template_name, t.version_no,
                   t.contract_type, t.business_type, t.auth_item,
                   a.doc_name, a.oss_url
            FROM contract_template t
            LEFT JOIN contract_attachment a ON a.source_id = t.id AND a.source_type = 3 AND a.deleted = 0
            WHERE t.deleted = 0 AND t.is_current = 1
            ORDER BY t.template_code
        """)
        rows = cur.fetchall()
    conn.close()

    ref_root = os.environ.get(
        "CONTRACT_KB_TEMPLATE_DIR",
        os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "参考"
        ),
    )
    missing_dir = os.path.join(ref_root, "数据库补齐模板_52份")
    all_cat_dir = os.path.join(ref_root, "全量标准模板库_按分类(78份)")
    os.makedirs(missing_dir, exist_ok=True)
    os.makedirs(all_cat_dir, exist_ok=True)

    downloaded_all = 0
    downloaded_missing = 0
    failed = []

    for r in rows:
        tcode = r["template_code"]
        if tcode == "MB20251226001":
            # Skip online edit test template
            continue

        tname = r["template_name"].strip()
        ver = r["version_no"].strip()
        url = (r["oss_url"] or "").strip()
        doc_name = (r["doc_name"] or "").strip()
        ext = os.path.splitext(doc_name)[1] or ".docx"
        if not ext.startswith("."):
            ext = "." + ext

        cat_folder = get_category_folder(r["business_type"], r["auth_item"])
        cat_dir = os.path.join(all_cat_dir, cat_folder)
        os.makedirs(cat_dir, exist_ok=True)

        std_fname = safe_filename(f"{tcode}_{ver}_{tname}{ext}")
        cat_target_path = os.path.join(cat_dir, std_fname)

        is_missing_in_ref = tcode not in REF_EXISTING_MAP

        try:
            req_url = encode_url(url)
            req = urllib.request.Request(req_url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=20) as resp:
                data = resp.read()
            with open(cat_target_path, "wb") as out_f:
                out_f.write(data)
            downloaded_all += 1

            if is_missing_in_ref:
                # Save into 参考/数据库补齐模板_52份 AND directly into 参考/ root so 参考/ is complete
                missing_fname = safe_filename(f"{tname}{ext}")
                missing_path = os.path.join(
                    missing_dir, safe_filename(f"{tcode}_{tname}{ext}")
                )
                with open(missing_path, "wb") as out_f:
                    out_f.write(data)
                ref_direct_path = os.path.join(ref_root, missing_fname)
                if not os.path.exists(ref_direct_path):
                    with open(ref_direct_path, "wb") as out_f:
                        out_f.write(data)
                downloaded_missing += 1

        except Exception as e:
            failed.append((tcode, tname, url, str(e)))

    print(f"Downloaded {downloaded_all}/78 official templates into {all_cat_dir}")
    print(
        f"Downloaded {downloaded_missing}/52 missing templates into {missing_dir} and {ref_root}"
    )
    if failed:
        print(f"Failed ({len(failed)}):")
        for item in failed:
            print(" ", item)


if __name__ == "__main__":
    main()
