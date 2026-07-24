# MVP 范围

## 1. MVP 目标

用一个真实项目中的 1-5 个可复核案例证明以下链路可重复执行；需要验证跨项目泛化时再增加设计书写法不同的第二个项目：

```text
设计书 before/after
  -> 正确 StructuredChange
  -> 真实 RAG Context Package
  -> 正确代码候选范围
  -> Impact Report 和批准
  -> Copilot 白名单修改
  -> OperaMind Playwright UI 验证
```

## 2. MVP 包含

- 一个项目可以配置多个 Document Convention Variant。
- 文件名、Sheet、标题、表头和业务词共同匹配设计书写法。
- 文件改名、Sheet 改名和列顺序调整不产生伪业务变化。
- PostgreSQL Canonical Snapshot 和 pgvector 真实向量索引。
- Vector + keyword hybrid ranking，只返回 Canonical ID。
- Tree-sitter Code Graph，多候选、正向依赖、反向调用方和测试映射。
- Impact Report、用户确认、Edit Packet 和 Git diff 范围校验。
- 复用或生成受影响 Verification Scenario，执行 Playwright 并保存证据。
- 一键 E2E 和分步手动期待值。

## 3. MVP 暂不包含

- 自动学习并直接激活 Profile。
- 复杂文档拆分、合并和跨文件移动推断。
- Neo4j。
- 多租户计费和企业权限体系。
- 大规模 ANN 集群和跨地域容灾。
- 全局多团队修改 DAG。
- IDE 自研代码编辑 Agent。

## 4. 关闭门禁

```text
Golden Dataset StructuredChange 匹配率 = 100%
可索引节点 Vector 覆盖率 = 100%
跨 Project/Snapshot RAG 命中数 = 0
Golden Dataset 必选代码文件 Recall = 100%
未知高影响项 = 0
范围外修改数 = 0
必需 UI 场景执行率 = 100%
必需 UI 场景通过率 = 100%
```

范围准确率和 RAG Recall@K 的目标阈值由 Golden Dataset 固化，不能通过修改期待值迁就当前实现。

`operamind-baseline --require-ready` 只证明 Golden Dataset 本身完成冻结；整个 MVP 还必须通过 `operamind-baseline --require-mvp-ready`。后者要求真实 Provider、人工批准、GitHub Copilot、绑定 Deployment E2E 和完整本地回归均有 digest 固定的最终证据。需要语义判断的证据记录人工确认，确定性回归记录机器验证；当前 source tree 的旧全回归 digest 已失效，且 `github_copilot_live` 外部 gate 仍为 `pending`。

## 5. 预计工作量

| 工作 | 人日 |
|---|---:|
| 开发与测试 | 24-37 |
| 架构、RAG、Profile、Copilot、UI 和 E2E 文档 | 8-13 |
| 合计 | 32-50 |

一人全职约 7-11 周；两人并行约 5-7 周。
