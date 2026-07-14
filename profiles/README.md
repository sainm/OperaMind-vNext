# Profile Examples

Profile 是运行时配置，不包含凭据。示例用于冻结 vNext 的配置方向，正式实现需要为 Profile 增加 JSON Schema、数据库版本记录和激活审计。

- `embedding-profile.example.json`：真实 RAG Provider 和批量索引参数。
- `document-convention-profile.example.json`：同类设计书的多种写法。
- `code-framework-profile.example.json`：代码扫描锚点和关系扩展策略。

项目可以组合多个 Document Convention 和 Code Framework Profile。项目领域名称不能写入通用引擎。
