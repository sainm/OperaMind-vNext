# Golden Dataset

Golden Dataset 是业务、开发和测试共同确认的标准答案，不是为了配合当前实现而调整的普通 fixture。

每个案例必须包含：

```text
before documents
after documents
target repository and fixed commit
expected Structured Changes
expected RAG section/chunk IDs
expected must-include and must-exclude code paths
expected UI scenarios and visible results
```

Manifest 通过 `source_manifest` 统一引用 before/after 文档来源和固定代码 Revision。`silver` 可以从一个待审项目开始；只有包含至少两个项目、记录 reviewers 且 `status=frozen` 的 Dataset 才能标记为 `golden`。

MVP 至少准备两个真实项目、十二个案例，覆盖：

- 只改文件名、Sheet 或列顺序，业务不变。
- API、DB、页面字段和业务规则变化。
- 多文档联合变化。
- 多代码文件影响。
- 无法自动判断、必须人工审阅。

真实设计书可以脱敏，但必须保留结构、关系和变化特征。代码仓库不复制到 Dataset，只保存 URL、commit、scan roots 和期待路径。
