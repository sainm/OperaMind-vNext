# 旧工程提炼清单

## 保留的设计原则

- Canonical DB 是业务事实来源。
- Vector Store 只是可重建 Search Index。
- RAG 候选必须回查数据库生成 Context Package。
- Code Graph 与目标 Repository Revision 强绑定。
- Impact Report、用户确认、Edit Packet 后才能修改代码。
- OperaMind 验证目标系统 UI，Copilot 只负责批准范围内的本地代码修改。
- 修改后代码验证和 Web UI 验证必须聚合关闭。

## 重新设计后保留的概念

- Project / Repository / Revision
- Document Snapshot / Structured Change
- Context Package
- Code Graph Snapshot / Test Binding
- Impact Report / Confirmation
- Edit Packet / Edit Result
- Verification Scenario / Trigger Path / UI Run / Final Validation

## 不直接复制

- Java Control Plane 实现。
- 旧 FastAPI、knowledge、worker 和 React 实现。
- P00-P12 生成脚本。
- 旧 Neo4j metadata 和 graph builder。
- 旧 `knowledge_chunks` RAG 主链路。
- 当前大型 Code Graph fixture 和生成结果。
- 当前完整 Schema Catalog。
- build、bin、cache、snapshot、node_modules 和虚拟环境。

## 可以作为参考但需要重写

- 增量 Document Snapshot 行为。
- Context Rebuilder 的父子、相邻和跨文档扩展规则。
- Tree-sitter 文件、符号和边提取策略。
- MCP 分析/确认/Edit Packet 工具边界。
- Playwright Run、Evidence 和 Final Coverage 语义。
- 手动 E2E 期待值格式。

## 迁移许可门禁

任何旧实现进入 vNext 前必须满足：

1. 属于 MVP 主链路。
2. 没有目标项目领域硬编码。
3. 有独立自动测试。
4. 符合 vNext 核心 Contract。
5. 代码量和依赖成本低于重写成本。
