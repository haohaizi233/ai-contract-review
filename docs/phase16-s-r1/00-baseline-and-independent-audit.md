# Phase 16-S-R1: 独立复验基线与架构调用链审计报告

## 一、审计基线与环境信息

- **代码仓库**: `https://github.com/haohaizi233/ai-contract-review`
- **现有 Pull Request**: `https://github.com/haohaizi233/ai-contract-review/pull/4`
- **目标整改分支**: `fix/phase16-trust-boundary-security`
- **基线审计 Commit**: `5cc7822bd71eca83daf0e38fb4752c9d37d6fd64`
- **运行环境**: Python 3.13.17, pytest 9.2.0, ruff 0.17.4, mypy 2.4.1 (Windows x64)
- **基线全量测试**: 523 项全部通过，但存在 8 项严重阻断性与伪阳性安全隐患

---

## 二、生产端到端调用链深度审计

真实业务端到端调用链路还原如下：

```text
企业用户发起审核 (HTTP POST /api/diff/upload 或 /api/diff/rerun_review)
        ↓
Web HTTP 请求认证 (contract_kb/interfaces/http/auth.py: authenticate_web_request)
        ↓
AuthenticatedPrincipal (解析 tenant_id, user_id, roles)
        ↓
Session 身份与租户归属校验 (contract_kb/application/agents/review_sessions.py: get_or_create_session)
        ↓
上传合同并生成 Diff (contract_kb/interfaces/web/services/diff_service.py: execute_current_diff)
        ↓
AgentReviewManager.start_document_review(diff_data, session_id, tenant_id, user_id)
        ↓
AgentReviewSession._background_worker_loop(current_gen)
        ↓
LegalReviewAgent.review_clause(annotation, doc_context, storage)
        ↓
GovernedRetrievalService / retrieve_governed_rules (租户与受控知识库)
        ↓
CitationVerifier (证据真实性、租户归属、原文哈希与审批状态核验)
        ↓
受控 LLM Provider (OpenAICompatProvider / Contract Text Nonce 信封隔离)
        ↓
模型结果强类型 Schema 结构化校验 (LegalAgentReviewResult)
        ↓
证据有效性二次验证 (TOCTOU 规则撤销与版本变更重验)
        ↓
审核状态持久化 (CheckpointStorage & annotations_map)
        ↓
SSE 实时推送 (AgentReviewSession.broadcast -> handle_get_agent_stream)
        ↓
人工复核与裁决 (handle_post_human_decision -> HumanReviewStatus)
        ↓
未审批用户反馈提交 (submit_feedback -> PENDING_REVIEW)
        ↓
独立授权审批服务 (approve_feedback_for_training -> APPROVED_FOR_TRAINING)
        ↓
按租户安全导出微调样本 (export_sft_dataset)
```

### 调用节点要素对照矩阵

| 调用节点 | 调用方 | 被调用方 | Tenant ID 来源 | User ID 来源 | Storage 来源 | 失败处理策略 | 审计风险 |
|---|---|---|---|---|---|---|---|
| HTTP 认证 | Web Handler | `authenticate_web_request` | HTTP Header (`X-Tenant`) | HTTP Header (`X-User`) | 无/全局 NonceStore | 抛出 PermissionError | 隐式依赖 sys.modules 查找 Storage |
| 会话调度 | Web Route | `AgentReviewManager` | 认证 Principal | 认证 Principal | **缺失** | 抛出 PermissionError | **P0: 未向 Manager 注入 Storage** |
| 异步 Worker | Session Thread | `review_clause` | 会话绑定 `self.tenant_id` | 会话绑定 `self.user_id` | **缺失 (None)** | 降级为 Fallback | **P0: 真实 Worker 丢失 Storage 依赖** |
| 规则检索 | LegalReviewAgent | `retrieve_governed_rules` | doc_context | doc_context | 来自 Agent 调用参数 | 生产模式返回空列表 | 缺少 Storage 时无法获取企业自定义规则 |
| 证据标记 | LegalReviewAgent | evidence_refs | 参数 `tenant_id` | N/A | Storage | 无校验直接标记 | **P0: APPROVED 直接标记为 VERIFIED** |
| 缓存核验 | LegalReviewAgent | `storage.get_rules` | 参数 `tenant_id` | N/A | 调用参数 Storage | `except Exception: pass` | **P0: 数据库异常放行过时缓存** |
| 模型推理后 | LegalReviewAgent | TOCTOU check | 参数 `tenant_id` | N/A | 调用参数 Storage | `except Exception: pass` | **P0: 数据库异常继续持久化缓存** |
| 反馈记录 | Web Route / Service | `record_user_feedback` | 认证 Principal | 认证 Principal | 磁盘 JSONL | 写入文件 | **P1: 服务层允许 payload 自审批** |
| 首页加载 | Web Route | `generate_rendered_html` | N/A | N/A | 磁盘/内存 | 返回 HTML | **P1: 未认证直接输出案例与模板元数据** |

---

## 三、八项缺陷审计清单与风险等级

1. **P0-R1-01 (P0 阻断)**: `AgentReviewSession._background_worker_loop` 未显式向 `LegalReviewAgent.review_clause` 传递 Storage，真实运行链路依赖丢失。
2. **P0-R1-02 (P0 阻断)**: `evidence_refs` 将 `approval_status == 'APPROVED'` 直接等同于 `verification_status = 'VERIFIED'`，绕过实际的原文、哈希与租户一致性 CitationVerifier。
3. **P0-R1-03 (P0 阻断)**: `review_clause` 缓存有效性校验中，`storage.get_rules` 发生异常时被 `except Exception: pass` 捕获，`cache_valid` 仍为 True，造成失效缓存放行。
4. **P0-R1-04 (P0 阻断)**: 模型推理完成后的 TOCTOU 审批撤销复核中，捕获异常后直接穿透执行缓存落盘与 `AI_REVIEW_COMPLETED` 返回。
5. **P1-R1-05 (P1 严重)**: `record_user_feedback` 允许通过传入 payload 中的 `is_approved=True` 直接将数据写入 `reviewed_events.jsonl`，缺少服务层授权阻断。
6. **P1-R1-06 (P1 严重)**: `test_client_cannot_self_approve_training_data` 断言 `doc_id` 不在 JSONL 中，而真实数据结构无 `doc_id` 字段，产生假阳性通过。
7. **P1-R1-07 (P1 严重)**: `InMemoryAndDbNonceStore` 在缺少显式 Storage 注入时通过 `sys.modules.get("contract_kb.api")` 猜测数据库，生产模式直接抛错阻断。
8. **P1-R1-08 (P1 严重)**: `generate_rendered_html(is_enterprise=True)` 仍然执行并嵌入了 `initial_cases` 与模板清单，导致未认证泄露。
