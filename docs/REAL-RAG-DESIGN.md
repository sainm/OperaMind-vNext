# 真实 RAG 设计

当前可执行实现、命令、验证证据和剩余边界见 `P2-REAL-RAG.md`。

当前主链路已实现到确定性的 Context Package，包括 Profile 驱动的 Relation Build、完整检索请求身份、Artifact SHA-256 读取复核、Golden Dataset 真实检索质量报告、Impact fail-closed 门和显式 opt-in 的真实 Provider live 合约测试入口。MVP 不依赖模型压缩；真实 Provider Evidence 与冻结 Golden 数据已经通过各自 readiness gate，冻结 Query 的 PostgreSQL/pgvector 回归也已通过。VisionDemo 运维数据库上的正式质量 Report 仍需在固定 Canonical Snapshot、本地当前 Embedding Profile 和 ready/current Index 可用时采集。

## 1. 正式主链路

```text
Canonical Snapshot committed
  -> Document Relation Build ready
  -> Embedding Build started
  -> eligible node coverage = 100%
  -> RAG index ready
  -> StructuredChange Query Plan
  -> Vector + Keyword retrieval
  -> section_id/chunk_id candidates
  -> Canonical DB rehydration
  -> parent/adjacent/related/cross-document expansion
  -> compressed Context Package
```

Fixture 和 keyword-only 只能用于测试或诊断。正式项目索引未 ready 时返回 `needs_rag_index` 或 `rag_index_failed`，不得生成可确认 Impact Report。

## 2. Embedding Profile

Profile 定义 provider 类型、配置变量名、模型、维度、批量大小、超时和重试策略。凭据只从环境或 Secret Manager 读取，不写入 Profile 或数据库。

MVP 支持 OpenAI-compatible HTTP Provider，并通过接口保留其他 Provider 扩展能力。运行前调用 health/model probe，实际返回维度必须与激活 Profile 一致。

## 3. 索引文本

每个 Section/Slice 的 embedding 输入由以下内容确定性组成：

```text
document type
heading path
business keys
section summary
slice content
relation labels
```

保存 input digest。相同 digest、模型和预处理版本复用 embedding，避免增量 Snapshot 重复计算。

Relation Build 必须先于 Embedding Build。索引记录其使用的 current Relation Build ID；关系规则更新会使旧索引 stale，防止 relation labels 与向量内容漂移。无唯一目标的规则结果进入 unresolved 台账，不生成推测边。

## 4. 混合检索

每个 Structured Change 至少生成：

- 业务行为查询
- 精确 API/DB/UI/规则锚点查询
- 验收标准查询

向量 Top-K 和关键词 Top-K 分别查询，再使用版本化 Reciprocal Rank Fusion 排序。检索结果包含：

```json
{
  "target_type": "section",
  "target_id": "section-123",
  "score": 0.91,
  "channels": ["vector", "keyword"],
  "source_query_id": "query-002"
}
```

结果不得包含正文。Context Rebuilder 按 ID 回查当前 Snapshot，并扩展父章节、相邻章节、关系章节以及 API、DB、UI 和验收上下文。

## 5. 隔离和门禁

- 检索必须过滤 Project、Document Snapshot、Embedding Profile 和 active status。
- Candidate ID 必须存在于 Snapshot Membership。
- Model 或 dimension 漂移导致索引 stale。
- 任何跨 Project/Snapshot 命中都属于数据隔离缺陷。
- Context Package 超 Token 时按变化组拆分，不截断 Canonical 候选账本。
- Context Package 固定 ingestion、Embedding Profile 版本/绑定、Top-K 和邻接距离；同 ID 参数漂移不是重放，必须失败。

## 6. 质量指标

Golden Dataset 持续计算：

- Recall@5 / Recall@10
- Mean Reciprocal Rank
- 必选上下文覆盖率
- 无关召回率
- 跨项目泄漏数
- Embedding 复用率
- 索引失败率和构建耗时

## 7. MVP 与完整版

MVP 使用 pgvector 精确余弦查询，适合早期数据量。完整版按模型和维度分区，并在质量验证后启用 HNSW/IVFFlat、异步重建和新旧模型在线切换。
