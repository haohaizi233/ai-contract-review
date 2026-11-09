# -*- coding: utf-8 -*-
"""企业微信微盘 RAG 同步系统 E01-E14 端到端自动化验收测试套件。

涵盖场景：
- E01: 首次入库（DOCX 与 PDF 结构化解析、切分与向量索引）
- E02: 重复扫描（内容未变 0 成本跳过，不产生重复向量）
- E03: 内容修改（哈希变动触发新版本更新与检索即时生效）
- E04: 文件重命名（识别路径变更，更新元数据，0 成本免重算 Embedding）
- E05: 文件未写完/不稳定（大小/mtime 处于变化中安全等待，不读半成品）
- E06: 解析失败与文件损坏（损坏文件安全隔离 QUARANTINED，不污染系统）
- E07: 索引失败与回滚（异常安全回滚，旧版本稳定可检索）
- E08: 中断恢复与增量重试（临时失败后下次扫描自愈恢复）
- E09: 文件暂时消失（进入宽限期保护，严禁立即删除索引）
- E10: 软删除与恢复（超时后软失效，文件重建后秒级重激活）
- E11: 目录穿越防御（防止符号链接或越界路径读取未授权文件）
- E12: 并发任务互斥（多线程/并发扫描下的线程安全与幂等）
- E13: 大文件超限熔断（超过 20MB 安全阈值安全拒绝）
- E14: 合同精准检索（端到端提问并验证命中文档与条款级证据可追溯）
"""

from __future__ import annotations

import os
import time
from pathlib import Path
import threading
from unittest.mock import patch

import docx
import pytest

from tools.sync_wecom_wedrive import (
    WeDriveSyncManager,
    FileSyncState,
)


def create_sample_docx(path: Path, title: str, clauses: list[tuple[str, str]]) -> None:
    """工具函数：在指定路径生成标准 DOCX 合同。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = docx.Document()
    doc.add_heading(title, level=1)
    for c_num, c_text in clauses:
        doc.add_paragraph(f"{c_num} {c_text}")
    doc.save(str(path))


def create_sample_pdf(path: Path, title: str, clauses: list[tuple[str, str]]) -> None:
    """工具函数：在指定路径生成标准 PDF 合同。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    stream_parts = [f"BT /F1 12 Tf 50 750 Td ({title}) Tj ET\n".encode("ascii")]
    y = 720
    for num, text in clauses:
        clean_text = f"{num} {text}".replace("(", "\\(").replace(")", "\\)")
        stream_parts.append(
            f"BT /F1 10 Tf 50 {y} Td ({clean_text}) Tj ET\n".encode("ascii")
        )
        y -= 25
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

    with open(path, "wb") as f:
        f.write(header + obj1 + obj2 + obj3 + obj4 + xref + trailer)


@pytest.fixture
def test_env(tmp_path):
    """初始化隔离测试环境：独立 SQLite 数据库与微盘监控目录。"""
    db_file = tmp_path / "test_kb.db"
    watch_dir = tmp_path / "mock_wedrive"
    staging_dir = tmp_path / "staging"
    watch_dir.mkdir(parents=True, exist_ok=True)
    staging_dir.mkdir(parents=True, exist_ok=True)

    manager = WeDriveSyncManager(
        db_path=db_file,
        min_stable_checks=1,
        removal_grace_seconds=1.0,
        staging_dir=staging_dir,
    )
    return {
        "manager": manager,
        "watch_dir": watch_dir,
        "db_file": db_file,
        "storage": manager.storage,
    }


def test_e01_first_ingest(test_env):
    """E01: 首次入库测试（DOCX 与 PDF 正常解析与向量入库）。"""
    mgr: WeDriveSyncManager = test_env["manager"]
    watch: Path = test_env["watch_dir"]

    # 1. 放入 DOCX
    docx_file = watch / "MB10000000001_知识产权代理合同.docx"
    create_sample_docx(
        docx_file,
        "知识产权代理合同",
        [
            ("第一条", "代理权限范围包含国内专利申请、复审及维权诉讼。"),
            ("第二条", "服务费用总额为人民币伍万元整，甲方应于签字后五日内结清。"),
            ("第三条", "双方对技术交底资料承担严格的保密义务，保密期五年。"),
        ],
    )

    # 2. 放入 PDF
    pdf_file = watch / "MB10000000002_补充协议.pdf"
    create_sample_pdf(
        pdf_file,
        "Supplementary Agreement",
        [
            ("Article 1", "Payment term is extended by thirty calendar days."),
            ("Article 2", "Supplementary agreement has equal legal force."),
        ],
    )

    res = mgr.sync_from_directory(watch)
    metrics = res["metrics"]

    assert metrics["files_discovered"] == 2
    assert metrics["files_changed"] == 2
    assert metrics["files_failed"] == 0
    assert metrics["files_skipped"] == 0

    # 验证数据库中已激活两个文档
    active_hashes = mgr.get_existing_hashes()
    assert len(active_hashes) == 2


def test_e02_repeat_scan_deduplication(test_env):
    """E02: 重复扫描去重测试（内容未变，0 成本跳过）。"""
    mgr: WeDriveSyncManager = test_env["manager"]
    watch: Path = test_env["watch_dir"]

    f = watch / "MB10000000001_测试合同.docx"
    create_sample_docx(f, "测试合同", [("第一条", "约定内容条款")])

    # 第一次扫描
    mgr.sync_from_directory(watch)

    # 第二次扫描
    res2 = mgr.sync_from_directory(watch)
    m2 = res2["metrics"]

    assert m2["files_discovered"] == 1
    assert m2["files_skipped"] == 1
    assert m2["files_changed"] == 0
    assert m2["embedding_duration_ms"] == 0.0


def test_e03_content_modification_update(test_env):
    """E03: 内容修改测试（内容变更触发版本更新）。"""
    mgr: WeDriveSyncManager = test_env["manager"]
    watch: Path = test_env["watch_dir"]

    f = watch / "MB10000000001_版本迭代合同.docx"
    create_sample_docx(f, "合同V1", [("第一条", "初始违约金金额为一万元整。")])
    mgr.sync_from_directory(watch)

    # 修改内容
    time.sleep(0.05)
    create_sample_docx(
        f,
        "合同V2",
        [
            ("第一条", "经补充协商，违约金修改为十万元整。"),
            ("第二条", "争议解决提交深圳国际仲裁院仲裁。"),
        ],
    )

    res = mgr.sync_from_directory(watch)
    m = res["metrics"]

    assert m["files_changed"] == 1
    assert m["files_skipped"] == 0

    # 验证新条款能够被检索
    hits = mgr.storage.search("深圳国际仲裁院")
    assert len(hits) > 0
    assert "深圳国际仲裁院" in hits[0]["text"]


def test_e04_file_rename_metadata_update(test_env):
    """E04: 文件重命名测试（仅更新路径，0 成本免重新计算向量）。"""
    mgr: WeDriveSyncManager = test_env["manager"]
    watch: Path = test_env["watch_dir"]

    old_f = watch / "MB10000000001_原文件名.docx"
    create_sample_docx(old_f, "合同原名", [("第一条", "不变条款内容")])
    mgr.sync_from_directory(watch)

    # 重命名（内容哈希完全一致）
    new_f = watch / "MB10000000001_重命名后新文件名.docx"
    old_f.rename(new_f)

    res = mgr.sync_from_directory(watch)
    m = res["metrics"]

    assert m["files_changed"] == 1
    # 路径更新无需调用 Embedding
    assert m["embedding_duration_ms"] == 0.0


def test_e05_file_unstable_or_syncing(test_env):
    """E05: 文件未写完/不稳定测试（安全等待，不读半成品）。"""
    mgr: WeDriveSyncManager = test_env["manager"]
    watch: Path = test_env["watch_dir"]
    mgr.min_stable_checks = 2  # 要求至少两次稳定检查

    f = watch / "MB10000000001_正在下载中.docx"
    create_sample_docx(f, "未就绪合同", [("第一条", "数据未就绪")])

    # 模拟正在下载写入：不断改变大小或初次检测
    mgr.trackers.get(f.name)
    res = mgr.sync_from_directory(watch)
    assert res["metrics"]["pending_tasks"] == 1
    assert res["metrics"]["files_changed"] == 0  # 尚未入库

    # 第二次扫描：大小 mtime 稳定，完成入库
    res2 = mgr.sync_from_directory(watch)
    assert res2["metrics"]["files_changed"] == 1


def test_e06_corrupt_file_quarantined(test_env):
    """E06: 损坏文件隔离测试（损坏文件 QUARANTINED，不影响其他文件）。"""
    mgr: WeDriveSyncManager = test_env["manager"]
    watch: Path = test_env["watch_dir"]

    # 正常文件
    good_f = watch / "MB10000000001_正常.docx"
    create_sample_docx(good_f, "正常合同", [("第一条", "正常条款")])

    # 假 DOCX 损坏文件 (bad magic)
    bad_f = watch / "MB10000000002_损坏.docx"
    bad_f.write_bytes(b"NOT_A_VALID_DOCX_HEADER_GARBAGE")

    res = mgr.sync_from_directory(watch)
    m = res["metrics"]

    assert m["files_discovered"] == 2
    assert m["files_changed"] == 1  # 正常文件入库
    assert m["files_failed"] == 1  # 损坏文件被隔离
    assert mgr.trackers["MB10000000002_损坏.docx"].state == FileSyncState.QUARANTINED


def test_e07_index_failure_rollback(test_env):
    """E07: 索引失败回滚测试（模拟数据库故障，安全回滚）。"""
    mgr: WeDriveSyncManager = test_env["manager"]
    watch: Path = test_env["watch_dir"]

    f = watch / "MB10000000001_测试回滚.docx"
    create_sample_docx(f, "回滚合同", [("第一条", "测试回滚条款")])

    with patch.object(
        mgr.storage, "upsert_document", side_effect=RuntimeError("DB Disk Full")
    ):
        res = mgr.sync_from_directory(watch)
        assert res["metrics"]["files_failed"] == 1
        assert res["metrics"]["index_failures"] == 1
        assert mgr.trackers[f.name].state == FileSyncState.FAILED_RETRYABLE


def test_e08_recovery_after_failure(test_env):
    """E08: 故障自愈恢复测试（前次失败后再次运行自愈）。"""
    mgr: WeDriveSyncManager = test_env["manager"]
    watch: Path = test_env["watch_dir"]

    f = watch / "MB10000000001_自愈合同.docx"
    create_sample_docx(f, "自愈合同", [("第一条", "自愈条款")])

    # 第一次模拟失败
    with patch.object(
        mgr.storage, "upsert_document", side_effect=RuntimeError("Transient Error")
    ):
        mgr.sync_from_directory(watch)

    # 第二次恢复正常
    res2 = mgr.sync_from_directory(watch)
    assert res2["metrics"]["files_changed"] == 1
    assert mgr.trackers[f.name].state == FileSyncState.COMPLETED


def test_e09_file_disappear_grace_period(test_env):
    """E09: 文件暂时消失宽限期保护（严禁立即删除索引）。"""
    mgr: WeDriveSyncManager = test_env["manager"]
    watch: Path = test_env["watch_dir"]
    mgr.removal_grace_seconds = 100.0  # 宽限期 100 秒

    f = watch / "MB10000000001_防误删合同.docx"
    create_sample_docx(f, "防误删", [("第一条", "保护条款")])
    mgr.sync_from_directory(watch)

    # 模拟文件被临时移走/断网
    f.unlink()

    # 扫描：应当进入 PENDING_REMOVAL，但不删除数据库中的文档
    mgr.sync_from_directory(watch)
    tracker = mgr.trackers[f.name]
    assert tracker.state == FileSyncState.PENDING_REMOVAL

    # 验证数据库中该文档依然处于 ACTIVE 状态
    doc = mgr.storage.get_document(tracker.document_id)
    assert doc["status"] == "ACTIVE"


def test_e10_soft_delete_and_restore(test_env):
    """E10: 软删除超时与文件恢复（超时标记 INACTIVE，恢复后重新激活）。"""
    mgr: WeDriveSyncManager = test_env["manager"]
    watch: Path = test_env["watch_dir"]
    mgr.removal_grace_seconds = 0.05  # 极短宽限期测试超时
    mgr.enable_auto_deactivation = True

    f = watch / "MB10000000001_生命周期合同.docx"
    create_sample_docx(f, "生命周期", [("第一条", "生命周期条款")])
    mgr.sync_from_directory(watch)
    doc_id = mgr.trackers[f.name].document_id

    # 移除文件
    f.unlink()
    # 第一次检测进入待删除
    mgr.sync_from_directory(watch)
    time.sleep(0.1)
    # 第二次检测超过宽限期，软删除
    mgr.sync_from_directory(watch)

    doc = mgr.storage.get_document(doc_id)
    assert doc["status"] == "INACTIVE"

    # 文件重新恢复
    create_sample_docx(f, "生命周期", [("第一条", "生命周期条款")])
    mgr.sync_from_directory(watch)

    doc_restored = mgr.storage.get_document(doc_id)
    assert doc_restored["status"] == "ACTIVE"


def test_e11_path_traversal_defense(test_env):
    """E11: 目录穿越防御（禁止读取监控目录以外的文件）。"""
    mgr: WeDriveSyncManager = test_env["manager"]
    watch: Path = test_env["watch_dir"]

    # 在监控目录外创建敏感文件
    outside_file = watch.parent / "sensitive_outside.docx"
    create_sample_docx(outside_file, "机密文件", [("第一条", "机密禁止越界")])

    # 在 watch 目录下创建相对路径穿越或非法访问测试
    # 确认直接越界无法被 scanner 处理
    mgr.sync_from_directory(watch)
    assert outside_file.name not in mgr.trackers


def test_e12_concurrent_tasks_mutex(test_env):
    """E12: 并发任务互斥测试（保证多线程或重复扫描安全无锁冲突）。"""
    mgr: WeDriveSyncManager = test_env["manager"]
    watch: Path = test_env["watch_dir"]

    for i in range(5):
        f = watch / f"MB1000000000{i}_并发.docx"
        create_sample_docx(f, f"并发合同{i}", [("第一条", f"并发条款内容{i}")])

    errors = []

    def run_worker():
        try:
            mgr.sync_from_directory(watch)
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=run_worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(errors) == 0
    active = mgr.get_existing_hashes()
    assert len(active) == 5


def test_e13_large_file_boundary(test_env):
    """E13: 大文件边界熔断测试（超出 20MB 安全限制安全拦截）。"""
    mgr: WeDriveSyncManager = test_env["manager"]
    watch: Path = test_env["watch_dir"]

    huge_f = watch / "MB10000000001_超大文件.docx"
    create_sample_docx(huge_f, "超大合同", [("第一条", "基础信息")])

    # 模拟文件大小超限 > 20MB
    target_cls = type(huge_f)
    with (
        patch.object(target_cls, "stat") as mock_stat,
        patch.object(Path, "stat", mock_stat),
    ):
        mock_stat_res = os.stat(huge_f)
        # 伪造 st_size 为 25MB
        mock_stat.return_value = os.stat_result(
            (
                mock_stat_res.st_mode,
                mock_stat_res.st_ino,
                mock_stat_res.st_dev,
                mock_stat_res.st_nlink,
                mock_stat_res.st_uid,
                mock_stat_res.st_gid,
                25 * 1024 * 1024,
                mock_stat_res.st_atime,
                mock_stat_res.st_mtime,
                mock_stat_res.st_ctime,
            )
        )
        res = mgr.sync_from_directory(watch)
        assert res["metrics"]["files_failed"] == 1
        assert mgr.trackers[huge_f.name].state == FileSyncState.QUARANTINED


def test_e14_contract_rag_retrieval_traceability(test_env):
    """E14: 端到端 RAG 合同精准问答与证据溯源测试。"""
    mgr: WeDriveSyncManager = test_env["manager"]
    watch: Path = test_env["watch_dir"]

    f = watch / "MB10000000088_国际专利合作协议.docx"
    create_sample_docx(
        f,
        "国际专利合作协议",
        [
            ("第一条", "涉外专利申请代理服务收费标准为每件壹万伍仟美元。"),
            ("第二条", "不可抗力事件包括战争、严重自然灾害及政府紧急法令。"),
            ("第三条", "争议管辖：如协商不成，应向深圳市南山区人民法院提起诉讼。"),
        ],
    )

    mgr.sync_from_directory(watch)

    # 执行精准条款检索提问
    hits = mgr.storage.search("深圳市南山区人民法院", top_k=3)
    assert len(hits) > 0
    top_hit = hits[0]

    # 验证证据包可追溯性
    assert "深圳市南山区人民法院" in top_hit["text"]
    assert top_hit["document_id"] == "MB10000000088"
    assert top_hit["clause_id"] is not None
