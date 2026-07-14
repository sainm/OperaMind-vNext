# Canonical Data Model

## 1. 原则

- 数据库保存业务事实、版本、引用和状态。
- JSON Artifact 用于模块交换，不替代规范化查询表。
- Vector Index、缓存、压缩摘要和运行日志可重建，不成为业务事实来源。
- 每个分析结果都绑定输入版本，历史记录不可原地改写。

## 2. MVP 表组

### Project 和 Repository

```text
projects
repositories
repository_revisions
analysis_cases
```

### Document

```text
documents
document_versions
document_nodes
document_facts
document_relations
document_snapshots
snapshot_memberships
document_profile_versions
```

`document_nodes` 使用树结构表达 section、paragraph、table、row、cell 和 slice。Chunk 是检索视图，必须引用原始节点和来源位置。

### Search Index

```text
embedding_profiles
search_index_builds
search_index_entries
document_search_vectors
```

向量表只保存目标 ID、Snapshot、内容 Hash、模型和 embedding。正文从 `document_nodes` 回查。

### Change 和 Context

```text
structured_changes
context_packages
```

Structured Change 保存 before/after Fact 引用和来源证据。Context Package 保存压缩事实和 ID 引用，不复制大量原文。

### Code Graph

```text
code_graph_snapshots
code_files
code_symbols
code_edges
code_test_bindings
```

不保存完整源代码。文件节点绑定 repository revision、path、content hash 和语言；symbol 保存稳定签名和行号范围。

### Impact 和 Edit

```text
impact_reports
impact_items
impact_confirmations
edit_packets
edit_results
```

Edit Result 保存 commit、changed paths、测试引用和越界文件，不保存完整 diff。

### UI Verification

```text
verification_scenarios
trigger_paths
ui_execution_plans
ui_execution_runs
ui_execution_evidence
change_validations
```

浏览器截图和大日志可以进入对象存储，但数据库必须保存不可变 Evidence Ref、Hash、环境和 Deployment Revision。

## 3. 关键唯一约束

- Snapshot 内 Document Membership 唯一。
- Snapshot、Target ID、Embedding Profile、Ranking Policy 的 Search Entry 唯一。
- Repository Revision、Path 的 Code File 唯一。
- Impact Report、Impact Item、Confirmation 的引用不能跨 Project。
- UI Run 的 Deployment Revision 必须与 Edit Result 对应 Build 一致。
