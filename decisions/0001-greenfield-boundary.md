# ADR-0001：采用 Greenfield vNext

## 状态

Accepted

## 决策

OperaMind vNext 建立为独立仓库，不复制旧工程运行代码。旧工程保持只读参考，直到 vNext MVP 通过 Golden Dataset 和真实 UI E2E 后再归档。

## 原因

- 旧工程同时包含多个历史 MVP、生成脚本、双后端路径和大量 Artifact，继续原地删除的风险高。
- vNext 的正式主链路要求 Snapshot 级真实 RAG，而旧 Canonical MCP 链路仍以关键词检索为主。
- UI 自动化能力应复用业务设计，不需要复制所有旧阶段代码。
- 新工程需要少量核心契约和清晰模块边界。

## 结果

- 新工程初始阶段只有设计、契约、Profile 和 Golden Dataset。
- 任何旧代码迁移必须有独立 ADR、测试证据和明确收益。
- 不以“旧工程已有”为理由复制未进入 MVP 主链路的模块。
