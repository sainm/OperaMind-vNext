# Code Graph 与修改范围

## 1. 首次载入

项目建立 Baseline 时，对明确绑定的目标 Repository Revision 扫描一次：

```text
detect stack
  -> select Code Framework Profiles
  -> confirm scan roots
  -> Tree-sitter parse
  -> Code Graph Snapshot
  -> Test Binding Index
```

扫描整个批准根目录不等于让 Copilot 读取整个仓库。Code Graph 是 Canonical 索引，Impact Report 最终只输出小型工作集。

## 2. 图节点和边

MVP 节点：file、symbol、endpoint、table、column、config key、UI route、test。

MVP 边：contains、imports、calls、implements、exposes、reads、writes、maps_to、tests、navigates_to。

每条边记录 extractor、Profile Version、置信度、源位置和目标解析状态。

## 3. Scope Resolver

```text
StructuredChange anchors
  -> multiple direct matches
  -> typed forward expansion
  -> reverse callers
  -> interface implementations
  -> DB/config/UI relations
  -> test bindings
  -> score and classify
```

关系扩展深度由 Code Framework Profile 定义，不在通用代码中硬编码 Spring、Expense 或文件后缀特例。

## 4. 输出范围

```json
{
  "editable_files": [],
  "read_only_files": [],
  "test_files": [],
  "unknown_items": [],
  "out_of_scope_policy": "stop_and_reanalyze"
}
```

每个候选必须带文档证据、直接代码锚点、图路径、距离、得分和建议动作。未知高影响项和 truncated graph 阻止确认。

## 5. 修改后刷新

Edit Result 产生新 commit 后，旧 Code Graph Snapshot 进入 stale。系统只解析变化文件和受影响关系，并为新 Repository Revision 建立 Snapshot；后续 Edit Packet 必须绑定新 Snapshot。
