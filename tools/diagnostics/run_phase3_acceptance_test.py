# -*- coding: utf-8 -*-
"""Phase 3 真实全链路 RAG 摄取、索引与检索追溯验收执行脚本。

测试内容：
1. 真实 DOCX 与 PDF 解析；
2. 8 阶段状态机运转（DISCOVERED -> READY -> PARSING -> CHUNKING -> EMBEDDING -> INDEXING -> COMPLETED）；
3. 256 维局部哈希向量嵌入与 SQLite FTS5 混合检索；
4. 追溯性验证：基于合同条款内容的高精度定位与元数据溯源。
"""

import json
from pathlib import Path
import sys
import time

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")
import docx

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from tools.sync_wecom_wedrive import WeDriveSyncManager, FileSyncState  # noqa: E402

VERIFY_DIR = ROOT_DIR / "tools" / "test_assets" / "verification_run"
STAGING_DIR = ROOT_DIR / "reports" / "sync_staging"
ACCEPT_DB = ROOT_DIR / "reports" / "phase3_acceptance_rag.db"

VERIFY_DIR.mkdir(parents=True, exist_ok=True)
STAGING_DIR.mkdir(parents=True, exist_ok=True)
if ACCEPT_DB.exists():
    try:
        ACCEPT_DB.unlink()
    except Exception:
        pass


def generate_test_assets():
    """生成合规、脱敏、无任何商业机密的标准测试合同文档。"""
    # 1. DOCX 框架协议
    docx_path = VERIFY_DIR / "MB20261109001_知识产权代理框架协议.docx"
    doc = docx.Document()
    doc.add_heading("知识产权代理框架协议（Phase3标准验收母本）", level=1)
    doc.add_paragraph(
        "第一条 合作范围：甲方委托乙方代理全球专利布局申请、商标注册维护及知识产权侵权分析业务。"
    )
    doc.add_paragraph(
        "第二条 代理费用结算：本协议服务费用采用年度框架结算制，累计服务费满人民币拾万元后十个工作日内电汇结清。"
    )
    doc.add_paragraph(
        "第三条 保密义务：双方对在履约过程中知悉的任何商业秘密及未公开技术交底书承担无限期保密义务。"
    )
    doc.add_paragraph(
        "第四条 争议解决条款：因履行本合同发生的一切争议，双方一致同意提交中国国际经济贸易仲裁委员会（CIETAC）在北京进行仲裁。"
    )
    doc.save(str(docx_path))

    # 2. PDF 补充协议
    pdf_path = VERIFY_DIR / "MB20261109002_合作补充协议.pdf"
    stream_parts = [
        b"BT /F1 12 Tf 50 750 Td (Supplementary Agreement for Intellectual Property Services) Tj ET\n",
        b"BT /F1 10 Tf 50 720 Td (Article 1 The term of intellectual property cooperation is extended to December 2028.) Tj ET\n",
        b"BT /F1 10 Tf 50 690 Td (Article 2 Exclusive Jurisdiction: Any legal lawsuit shall be filed in the People Court of Nanshan District Shenzhen.) Tj ET\n",
    ]
    stream = b"".join(stream_parts)
    header = b"%PDF-1.4\n"
    obj1 = b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"
    obj2 = b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n"
    obj3 = (
        b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R "
        b"/Resources << /Font << /F1 << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> >> >> >>\nendobj\n"
    )
    obj4 = (
        b"4 0 obj\n<< /Length "
        + str(len(stream)).encode("ascii")
        + b" >>\nstream\n"
        + stream
        + b"endstream\nendobj\n"
    )
    pos1 = len(header)
    pos2 = pos1 + len(obj1)
    pos3 = pos2 + len(obj2)
    pos4 = pos3 + len(obj3)
    xref_pos = pos4 + len(obj4)
    xref = f"xref\n0 5\n0000000000 65535 f \n{pos1:010d} 00000 n \n{pos2:010d} 00000 n \n{pos3:010d} 00000 n \n{pos4:010d} 00000 n \n".encode(
        "ascii"
    )
    trailer = (
        f"trailer\n<< /Size 5 /Root 1 0 R >>\nstartxref\n{xref_pos}\n%%EOF\n".encode(
            "ascii"
        )
    )
    with open(pdf_path, "wb") as f:
        f.write(header + obj1 + obj2 + obj3 + obj4 + xref + trailer)

    # 3. 准备一份用于真实微盘客户端测试的独立测试合同
    user_test_dir = ROOT_DIR / "tools" / "test_assets" / "user_client_manual_test"
    user_test_dir.mkdir(parents=True, exist_ok=True)
    user_docx = user_test_dir / "TEST_RAG_增量测试合同_v1.docx"
    u_doc = docx.Document()
    u_doc.add_heading("企业微信微盘增量同步验收测试合同（v1版本）", level=1)
    u_doc.add_paragraph(
        "第一条 验收目的：验证企业微信客户端新建合同能否秒级自动同步到本地并入库 RAG 知识库。"
    )
    u_doc.add_paragraph(
        "第二条 独特特征标记：验收代码包含特征指纹 WEWORK_WEDRIVE_ACCEPTANCE_TEST_KEY_20261109。"
    )
    u_doc.add_paragraph(
        "第三条 仲裁管辖：因本测试合同引发的一切技术交流，在深圳前海国际仲裁院协商处理。"
    )
    u_doc.save(str(user_docx))

    print(
        f"✅ 生成脱敏测试资产完成：\n  - {docx_path.name}\n  - {pdf_path.name}\n  - {user_docx.name}"
    )


def run_acceptance():
    print("🚀 开始执行 Phase 3 端到端真实管道全流程验收...")
    generate_test_assets()

    mgr = WeDriveSyncManager(
        db_path=ACCEPT_DB,
        min_stable_checks=1,
        removal_grace_seconds=60.0,
        enable_auto_deactivation=False,
        staging_dir=STAGING_DIR,
    )

    t0 = time.perf_counter()
    res = mgr.sync_from_directory(VERIFY_DIR)
    round((time.perf_counter() - t0) * 1000, 2)

    print("\n--- 1. 同步执行结果 ---")
    print(json.dumps(res, ensure_ascii=False, indent=2))

    # 检查状态机与文件追踪
    print("\n--- 2. 状态机追踪状态 ---")
    for name, tracker in mgr.trackers.items():
        print(f"  [{tracker.state.value}] {name} (SHA-256: {tracker.sha256[:12]}...)")
        assert tracker.state == FileSyncState.COMPLETED, f"Tracker {name} not COMPLETED"

    # 执行 FTS5 + 向量检索精准验证
    print("\n--- 3. 真实 RAG 条款混合检索与追溯验证 ---")
    storage = mgr.storage

    # 检索 1：DOCX 仲裁条款精准匹配
    query1 = "中国国际经济贸易仲裁委员会"
    hits1 = storage.search(query1, top_k=3)
    print(f"Query 1: '{query1}' -> 命中数: {len(hits1)}")
    assert len(hits1) > 0, "Query 1 failed to hit"
    top1 = hits1[0]
    print(
        f"  Top 1: doc={top1['document_id']}, clause={top1['clause_id']}, text={top1['text']}"
    )
    assert "中国国际经济贸易仲裁委员会" in top1["text"]
    assert top1["document_id"] == "MB20261109001"

    # 检索 2：DOCX 保密义务条款
    query2 = "商业秘密 未公开技术交底书"
    hits2 = storage.search(query2, top_k=3)
    print(f"Query 2: '{query2}' -> 命中数: {len(hits2)}")
    assert len(hits2) > 0, "Query 2 failed to hit"
    top2 = hits2[0]
    print(
        f"  Top 1: doc={top2['document_id']}, clause={top2['clause_id']}, text={top2['text']}"
    )
    assert "保密" in top2["text"]
    assert top2["document_id"] == "MB20261109001"

    # 检索 3：PDF 条款追溯
    query3 = "Nanshan District Shenzhen"
    hits3 = storage.search(query3, top_k=3)
    print(f"Query 3: '{query3}' -> 命中数: {len(hits3)}")
    assert len(hits3) > 0, "Query 3 failed to hit"
    top3 = hits3[0]
    print(
        f"  Top 1: doc={top3['document_id']}, clause={top3['clause_id']}, text={top3['text']}"
    )
    assert "Nanshan District" in top3["text"]
    assert top3["document_id"] == "MB20261109002"

    # 二次扫描去重验证（0成本跳过）
    print("\n--- 4. 二次增量扫描去重性能验证 ---")
    res2 = mgr.sync_from_directory(VERIFY_DIR)
    print(
        f"二次扫描指标: changed={res2['metrics']['files_changed']}, skipped={res2['metrics']['files_skipped']}, 耗时={res2['metrics']['scan_duration_ms']}ms"
    )
    assert res2["metrics"]["files_changed"] == 0
    assert res2["metrics"]["files_skipped"] == 2

    # 验证健康检查报告落盘
    health_file = STAGING_DIR / "sync_health.json"
    assert health_file.exists(), "Health file not written"
    print("\n--- 5. 健康检查文件写入 ---")
    print(health_file.read_text(encoding="utf-8"))

    print("\n🎉 Phase 3 真实全链路 RAG 管道完整验收全部通过！")


if __name__ == "__main__":
    run_acceptance()
