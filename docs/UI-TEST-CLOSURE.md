# UI 测试与关闭

## 1. UI 测试来源

设计书中的页面、操作和可见结果先归一化为业务场景：

```text
StructuredChange
  -> affected UI behavior
  -> Verification Scenario
  -> Trigger Path
  -> UI Execution Plan
```

设计书不负责提供 CSS Selector。Locator 由当前部署的 UI Knowledge 和运行时页面解析。

## 2. MVP 验证

修改前执行 Preflight：

- 环境和 Deployment 可访问
- 登录可用
- 测试数据就绪
- 页面和 Trigger Path 可执行
- Locator 达到项目可靠性要求

修改后执行：

- 每个受影响业务行为的必需 Scenario
- 由同一 API、页面或数据对象推导出的少量回归 Scenario
- 截图、步骤结果、业务断言和失败分类

## 3. 证据链

```text
StructuredChange
  -> ImpactItem
  -> VerificationScenario
  -> UiExecutionPlan
  -> UiExecutionRun
  -> Step/Assertion/Screenshot Evidence
  -> ChangeValidationResult
```

非 UI 变化必须明确记录 `not_impacted` 和判断依据，不能留空。

## 4. 失败分类

- business_assertion：业务行为未满足。
- environment：目标系统、浏览器或网络不可用。
- test_data：前置数据不满足。
- locator：元素定位失效或不可靠。
- authentication：登录或权限失败。
- blocked：分析信息不足，未启动浏览器。

只有 business assertion 才可以直接作为代码行为失败。其他失败先修复运行条件，再重新执行。

## 5. 关闭门禁

Change Validation 通过必须同时满足：

- Edit Result commit 与部署 Build 一致。
- Git diff 没有越界文件。
- 必需 UI Scenario 全部执行并通过。
- 每个批准 Impact Item 有浏览器证据或明确的非 UI 证据。
- 没有未解释的 skipped、blocked 或未知高影响项。
