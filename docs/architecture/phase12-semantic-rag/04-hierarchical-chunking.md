# Phase 12-C: 多粒度法律条款 Chunking 与上下文树架构规范

- **状态**: IMPLEMENTED & TESTED
- **核心组件**: [`contract_kb/modules/retrieval/hierarchy.py`](file:///d:/Users/Administrator/Desktop/AI合同/contract_kb/modules/retrieval/hierarchy.py)
- **测试用例**: [`tests/unit/test_clause_hierarchy.py`](file:///d:/Users/Administrator/Desktop/AI合同/tests/unit/test_clause_hierarchy.py)

---

## 1. 架构目标与五级知识粒度
合同不能像通用网页文本那样按固定 512 Token 切割。在严肃合同中，子条款（如 5.2 款）的法律效力往往受所属章条（如第 5 条）前提与免责约束。本项目构建了五级多粒度知识树：

```text
Contract Document (DOCUMENT: 整份合同路由/类型/主体)
 ├─ Chapter / Section (ARTICLE: “第五条 违约责任”)
 │   ├─ Subclause (SUBCLAUSE: 5.1 款首期支付义务)
 │   ├─ Subclause (SUBCLAUSE: 5.2 款逾期违约责任与限制)
 │   └─ Semantic Unit (SEMANTIC_UNIT: 长条款内部不可分割的权利/例外组合)
 └─ Structured Table (TABLE: 费用清单与服务等级表，保留二维坐标与表头)
```

## 2. Parent-Child 上下文关联机制
每个 `ClauseNode` 维护全双向关系拓扑：
- `parent_id`：指向所属父级章条或合同根；
- `child_ids`：按原始阅读顺序维护子款项列表；
- `previous_id` / `next_id`：维护同章节相邻款项的链表拓扑；
- `relations`：挂载本节点提取的轻量法律关系集合；
- `source_node_ids` / `source_locator`：严密锚定至 OOXML AST 原始节点。

## 3. 上下文扩展（Context Enrichment）流程
1. 检索通道命中细粒度 `SUBCLAUSE`（如 5.2 款）；
2. 通过 `parent_id` 自动溯源至所属 `ARTICLE`（第 5 条），提取整章通则生效前提与免责例外；
3. 解析条款中的 `CROSS_REFERENCE` 关系（如“依照第十二条执行”），向外单跳关联第十二条核心条款；
4. 输出结构化增强上下文包，注入归属章节、前置前提、正文及援引条目，杜绝“断章取义”。
