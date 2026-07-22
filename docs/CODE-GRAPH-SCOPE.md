# Code Graph 与修改范围

当前可执行数据模型、扫描与 Scope 命令、验证证据和剩余边界见 `P3-CODE-GRAPH.md`。目前已完成 revision-bound Tree-sitter Scanner、不可变 Graph Snapshot 持久化和 evidence-bound 双向 Scope Resolver；Scope 输出仍是 ImpactReport 之前的只读候选台账。

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
  -> validate ContextPackage evidence
  -> multiple direct matches
  -> typed forward expansion
  -> reverse callers
  -> interface implementations
  -> DB/config/UI relations
  -> test bindings
  -> score and classify
```

关系扩展深度由 Code Framework Profile 定义，不在通用代码中硬编码 Spring、Expense 或文件后缀特例。

锚点必须显式声明 namespace（path、symbol、endpoint、table、config key 或 UI route），并引用当前 ContextPackage 中存在的 evidence。Resolver 只在当前 Graph Snapshot 内精确匹配；缺失或溢出不会退化为模糊猜测。

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

直接命中的 production File 是 editable；只经关系扩展到达的 production File 是 read-only；test File 只来自显式 Test Binding。Scope 自身不批准 Edit Packet，也不扩大 Copilot allowlist。

## 5. 修改后刷新

Edit Result 产生新 commit 后，Scanner 默认从同 Repository、同 scan roots、同 Code Framework Profile 的当前 Snapshot 做 Revision 增量刷新：

```text
git diff --name-status --find-renames <base> <HEAD>
  -> 读取并解析新增／修改／重命名后的文件
  -> 比较旧、新声明集合
  -> 加入引用旧符号的反向依赖
  -> 声明变化时加入 unresolved 调用和可能转为 local 的 import
  -> 重建受影响节点、边和 tests 关系
  -> 复用其余文件、符号、边
  -> 发布绑定新 Revision 的完整不可变 Snapshot
```

删除和重命名会移除旧路径节点并重新计算反向依赖及 Test Binding；只有方法体变化而声明不变时，不会重新解析调用该方法的测试文件。Workspace Reader 对增量路径使用 Git 的明确路径集合，不遍历并读取其余源码。

启用 `spring_config_binding`、`spring_data_access` 或 `web_ui_route` 时，关系唯一性跨配置、UI、Controller、Entity、Repository 与 SQL 文件成立。Revision 有变化时 Scanner 会重新读取批准 scan roots 与 Git tracked file 的交集来重算这些关系；无变化 Revision 仍复用全部文件。该保守边界避免部分更新把旧跨层边误报为 current。

每个 Snapshot 在 Artifact 与 `code_graph_scan_lineage` 中记录 `scan_mode`、基线 Snapshot、Git 变更路径、实际影响路径、解析文件数和复用文件数。若基线不是当前 Revision 的祖先、Profile 或 scan roots 改变、或用户指定 `--full-scan`，系统安全回退为全量扫描，不复用不可信事实。旧 Snapshot 在新 Snapshot 原子发布后进入 stale；后续 Impact 和 Edit Packet 只绑定新 Snapshot。
