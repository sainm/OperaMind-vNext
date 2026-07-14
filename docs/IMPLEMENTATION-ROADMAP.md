# 实施路线

## 1. 开始前

- 选择两个设计书写法不同的真实项目。
- 为每个项目准备 before/after 文档、固定代码 commit、正确影响文件和 UI 场景。
- 人工审阅并冻结 Golden Dataset v1。
- 确认 Embedding Provider、目标 Web 环境和测试数据准备方式。

## 2. MVP 阶段

### P0：契约和数据基线

冻结八个核心 Artifact v1，建立 PostgreSQL migration、Repository round-trip 和 Golden Dataset 校验入口。

### P1：动态设计书写法和语义 Diff

实现 Convention Profile 多 Variant、低置信度审阅、Stable Key 和结构变化不变性测试。

### P2：真实 RAG

实现 Embedding Provider、增量索引、Snapshot 门禁、hybrid retrieval、Canonical rehydration 和 Recall@K 验收。

### P3：Code Graph Scope

实现 Framework Profile、Tree-sitter Snapshot、双向范围查询和 Test Binding。

### P4：Impact、MCP 和 Copilot

实现 Impact Report、确认、Edit Packet、workspace 校验和 edit result。

### P5：UI 验证

实现 Scenario 关联、Preflight、Playwright Run、Evidence 和 Change Validation。

### P6：E2E

两个 Golden Dataset 项目从文档导入运行到 UI 关闭，并提供一键及手动期待值。

## 3. 完整版增量

- Canonical Profile Registry 和 Profile Drift。
- 文档拆分、合并和复杂移动。
- 大批量 Change Group、Coverage Ledger 和全局修改计划。
- 生产级 ANN、模型切换、成本和质量监控。
- Packet 级 UI Smoke、Red Test 和受影响范围回归。
- 多租户权限、恢复、审计和容量治理。

## 4. 工期

| 版本 | 人日 | 一人全职 |
|---|---:|---:|
| MVP | 32-50 | 7-11 周 |
| 完整版总量 | 67-103 | 14-21 周 |
| MVP 后升级 | 35-53 | 7-11 周 |

## 5. 阶段交付物

每个阶段必须同时提交：实现、migration、核心契约更新、自动测试、手动期待值、运行说明和已知限制。不得把文档和 Golden Dataset 留到最后补写。
