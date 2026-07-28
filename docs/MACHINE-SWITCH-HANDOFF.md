# 机器切换后续交接

本交接用于在 Windows + WSL2 目标环境继续 `feature/reconstruct`。当前 macOS 环境只完成 OperaMind 本体的重构和内部回归，不把本机结果当作目标 Spring Boot 工程的最终 Evidence。

## 当前冻结前状态

- 分支：`feature/reconstruct`
- 工作区：尚未提交，存在大规模重构差异；切换机器前必须先提交并推送，或完整迁移当前工作目录
- 产品入口：Web 自然语言 `ChangeRequest`
- Web 展示：需求、设计书差异、代码范围、编译测试、UI 验证、最终报告六个阶段
- 编辑 AI：仅使用 VS Code 上的 GitHub Copilot
- Copilot MCP：仅公开五个统一 Change Task Tool
- 内部控制：Approval、Edit Packet、Queue、Lease、Worker、TestDataPlan 执行和 Closure 自动处理，不提供操作页面
- 当前内部回归：641 passed、4 skipped、总 coverage 82.59%；4 个 skip 都需要真实 Microsoft Edge
- 关键模块 coverage：全部逐文件达到 80%，其中 Copilot Change Task 81.16%、Main Flow Coordinator 83.78%、TestData／UI／Closure 执行 87.93%
- 静态和基线：Ruff、Mypy（183 个 source files）、VSIX（8 tests）、Node syntax、`git diff --check`、OperaMind baseline 均通过
- 显示边界：Web 只接受六阶段及其业务字段白名单；五个 Copilot MCP Tool 的 Context、命令、差异和结果响应以及 Bridge 通知均采用业务字段白名单，不返回 Automation Run、Edit Packet、Approval、Task、Lease、Worker、Snapshot、Search Index 或内部授权 ID

当前结果仍不是最终源码基线。没有提交 SHA 时，不得生成或宣称绑定当前重构的正式 `full_local_regression` Evidence。

## 待完成任务

以下项目按执行顺序排列。切换机器后从第一个未勾选项继续，不需要重新进行已经通过的 macOS 内部重构。

- [ ] **保存当前重构差异**：在原机器审查后提交并推送 `feature/reconstruct`；如果不能推送，迁移包含未跟踪文件的完整工作目录。当前差异尚未提交，只在新机器重新克隆分支会丢失本次重构。
- [ ] **准备 WSL2 运行环境**：在 WSL 文件系统内检出同一提交，运行安装脚本，启动 PostgreSQL，应用 migration，并确认 OperaMind Web 可访问。
- [ ] **恢复或重建 Canonical Data**：按需要恢复数据库迁移 Bundle；否则重新注册目标 Project／Repository，导入设计书并建立 current/ready RAG Index。旧机器的 Token 和凭据不得迁移。
- [ ] **绑定真实目标工程**：登记 Spring Boot 1.5／Thymeleaf／Gradle 工程代码目录和独立设计书目录，确认 Git Revision、Gradle Wrapper、Code Graph、Framework Profile 和 Command Profile。
- [ ] **接通 VS Code GitHub Copilot**：在 WSL Remote 窗口安装 Copilot 与 OperaMind VSIX，生成新的 Bridge Token，确认五个统一 Change Task MCP Tool 可用。
- [ ] **执行一次真实变更闭环**：从 Web 输入自然语言需求，由 Copilot 修改设计书、记录设计差异、修改限定代码、生成 TestPlan／TestDataPlan，并由 OperaMind 自动完成后续阶段。
- [ ] **采集真实 Gradle Evidence**：通过固定 `command_ref` 执行 compile、test、build，三项均须 exit code 0；不得用手工命令结果替代正式 Command Evidence。
- [ ] **执行 Edge UI 自动化**：安装 Linux 版 Microsoft Edge，启用 live Playwright，让当前 4 个 skip 全部实际执行；验证跨画面测试数据、UI Step、Assertion、Cleanup 和 Screenshot。
- [ ] **生成最终报告和 Evidence**：六阶段全部 `completed`，最终报告无未解决项；完整回归达到零 failure、零 skip，并重新生成绑定最终提交和目标 Deployment 的 readiness Evidence。
- [ ] **冻结新源码基线**：提交并推送最终源码与 Evidence，记录最终 Commit SHA，确认工作区干净。

当前不能在 macOS 完成的是目标 Spring Boot 工程的真实 Gradle 构建、WSL Linux Edge UI 测试、真实 VS Code Copilot 会话以及绑定目标提交的最终 Evidence；它们不是内部单元测试可以替代的项目。

## 切换前必须保存的内容

推荐先在原机器审查、提交并推送：

```bash
git status --short
git diff --check
git add -A
git commit -m "refactor: unify the Copilot main change flow"
git push -u origin feature/reconstruct
```

如果暂时不推送，必须迁移包含未跟踪文件的完整工作目录；只复制 `git diff` 不会包含新增文件。不要迁移 `.env*`、Bridge Token、数据库密码、API Key、浏览器 Storage State 或 `.venv`。

需要保留旧 Canonical DB 时，按 [WSL2 / Podman セットアップ](WSL-PODMAN-SETUP.md) 使用 `scripts/migrate-environment.sh` 导出、校验并恢复迁移 Bundle。Bundle 不包含凭据，目标环境必须生成新的 `.env.wsl` 和 Bridge Token。

## 目标环境准备

1. 在 WSL 文件系统中检出分支，不要把工程放在 `/mnt/c`。
2. 执行 `./scripts/install-wsl.sh install`。
3. 执行 `./scripts/install-wsl.sh start`，确认 PostgreSQL、Migration 和 `http://127.0.0.1:8765` 正常。
4. 在 Windows VS Code 中通过 WSL 打开目标 linked worktree，安装 GitHub Copilot 和生成的 OperaMind VSIX。
5. 将 `.env.wsl` 中新的 `OPERAMIND_BRIDGE_TOKEN` 登记到 VS Code SecretStorage，不复制旧 Token。
6. 恢复 Canonical DB，或注册新的目标 Project／Repository，并导入、索引目标设计书。

目标代码目录和设计书目录可以不同。代码目录必须登记为 Project 的 Repository Workspace；设计书通过 Canonical ingestion 保存原始引用。不要为了方便把设计书复制进代码仓库，也不要把 Windows 绝对路径固化到 Contract、Profile 或源码。

## Spring Boot 工程确认

在开始正式变更前确认：

- Repository 是干净的 linked worktree，并固定当前 Git Revision。
- 根目录存在 Gradle Wrapper。
- 工程实际使用 Spring Boot 1.5、Thymeleaf 和 Gradle。
- `src/main`、`src/test` 以及 Thymeleaf Template 均在 Code Graph 扫描范围内。
- Project 只有一个有效的 Code Framework Profile 和 Command Execution Profile。
- OperaMind 自动识别并绑定 `springboot15-thymeleaf-gradle` Profile；已有有效绑定不得被覆盖。

正式命令必须由 Change Task 中的固定 `command_ref` 调用：

| Command ref | 固定命令 |
|---|---|
| `springboot15-compile` | `./gradlew classes testClasses --no-daemon` |
| `springboot15-test` | `./gradlew test --no-daemon` |
| `springboot15-build` | `./gradlew build --no-daemon` |

手动执行可以用于环境诊断，但不能代替 `copilot_run_task_command` 保存的正式 Command Evidence。不要升级 Spring Boot、Thymeleaf、Gradle 或 Java 版本来绕过目标工程错误。

## 真实 Copilot 闭环

1. 在 Web 创建一条范围明确的自然语言变更需求。
2. VS Code Bridge 领取任务，用户在对话中确认一次任务范围。
3. GitHub Copilot 调用 `copilot_get_coding_task`。
4. Copilot 修改 RAG 找到的设计书，并以 `document_change` 记录实际设计差异。
5. Copilot 提出代码候选；OperaMind 用当前 Code Graph、Revision 和 Test Binding 验证 `code_scope`。
6. Copilot 只修改允许路径，生成自然语言 TestPlan／TestDataPlan，并记录 `test_planning`。
7. Copilot 调用固定 Gradle命令、验证工作区差异并记录最终结果和 changed-line coverage。
8. 内部 Coordinator 自动预约并执行 TestDataPlan、有限 UI Step／Assertion、Screenshot 和 Closure。
9. Web 六个阶段最终进入 `completed`，`final_report` 没有未解决项。

不得恢复旧 Case、Impact、Approval、Edit Packet、UI Plan 或 Validation Result 的独立 MCP／Web 操作入口，也不得把内部 Automation／Scheduler 字段加入通用前端渲染或 Copilot Tool 返回。

## Edge 与最终 Evidence

目标环境必须存在 Linux 版 Microsoft Edge，并使用：

```bash
export OPERAMIND_PLAYWRIGHT_LIVE=1
export OPERAMIND_PLAYWRIGHT_CHANNEL=msedge
```

以下 4 个当前跳过的 live 测试必须变为实际执行并通过：

- `test_main_change_flow_ui.py`
- `test_playwright_browser_executor.py` 的 3 个 live 场景

完成真实 Copilot 会话、Gradle 和 Edge 验证后，以目标 Project ID 和 Analysis Case ID 执行：

```bash
bash scripts/regenerate-readiness-wsl.sh <PROJECT_ID> <ANALYSIS_CASE_ID>
```

最终验收要求：

- 完整回归为零 failure、零 skip。
- Gradle compile、test、build 都由固定 Command Profile 返回 exit code 0。
- UI Scenario、Assertion、Cleanup 和 Screenshot Evidence 完整。
- `github_copilot_live`、`target_deployment_e2e`、`full_local_regression` Evidence 都绑定最终源码提交和目标 Deployment。
- 以下 readiness 门禁通过：

  ```bash
  .venv/bin/operamind-baseline \
    --manifest golden-dataset/manifest.golden.json \
    --readiness-manifest readiness/mvp-readiness.json \
    --require-mvp-ready
  ```
- Evidence 生成后未再修改源码、测试、Migration、Contract、Profile、依赖锁或质量策略。
- 最终 Evidence 另行提交，工作区恢复干净。

## 恢复工作时的首要检查

```bash
git branch --show-current
git status --short
git rev-parse HEAD
./scripts/install-wsl.sh status
.venv/bin/operamind-baseline --print-readiness-json
```

若其中任一项与本交接不一致，先查明是分支、源码、Canonical DB、Profile、Edge 还是 Evidence 漂移，不要直接把 readiness 状态改成 `passed`。
