# 単機版 VS Code 統合将来構想

## 状態

この文書は、現在の単機 Desktop 版をより簡単に利用するための将来構想を保存するものであり、**現時点では実装しない**。Server 版の設計とは分離し、ユーザー認証、Organization、Remote Agent、Remote MCP は本構想の Scope に含めない。

現在の `OperaMind.app`／`OperaMind.exe`、Loopback Web、stdio MCP、外部 Browser 画面は引き続き正式な実装である。本構想を理由に、現在の Packaging、Runtime Manifest、Bridge Token、MCP、Web、手動テスト手順を完了扱いにしたり変更済みと記録したりしない。

## 目的

単機版の利用者が扱う配布物と入口を次の二つへ整理する。

```text
OperaMind.exe または OperaMind.app
operamind-copilot-bridge.vsix
```

利用者は VS Code の OperaMind 画面を主入口として、Project、変更要件、確認、TestPlan、UI Test、Evidence、Reportを操作する。独立した `OperaMindMcp.exe` は廃止し、外部 Browser 版 Web は任意の全画面表示／診断入口として残す。

## 目標アーキテクチャ

```text
VS Code GitHub Copilot
  │
  ├─ OperaMind VS Code Extension
  │    ├─ Activity Bar／Webview UI
  │    ├─ Local Backend の起動確認
  │    ├─ Bridge Token の SecretStorage
  │    └─ Loopback HTTP MCP の登録
  │
  └──────────────┐
                 ▼
OperaMind Local Backend
  ├─ FastAPI REST API
  ├─ MCP Streamable HTTP
  ├─ PostgreSQL／Migration
  ├─ Canonical Data／RAG／Code Graph
  ├─ Workflow／Confirmation／Coordinator
  ├─ Compile／Test／Coverage
  ├─ TestData／Playwright／Cleanup
  └─ Evidence／Report
```

VS Code Extension は表示、現在 Workspace、Copilot 連携、MCP 定義、ユーザー操作だけを担当する。Domain Validation、Artifact、Database、Migration、Command、Playwright を JavaScript へ複製しない。

## Web の統合

OperaMind Web の表示機能を VS Code Webview から利用できるようにする。

主な画面は次のとおりとする。

- Project 選択、初期化、設定、Preflight
- 変更要件登録と六工程の進行状況
- RAG 文書候補と確認対象
- Code Graph とコード影響範囲
- 自然言語 Test Case、UiTestPlan、TestDataPlan
- 既存テストデータ、固定データ識別子、Cleanup
- Confirmation、阻断理由、次の操作
- Playwright 実行状態、Screenshot、Evidence
- ChangeClosureResult と最終 Report

狭い Activity Bar には要約と主操作だけを表示する。Code Graph、TestPlan、Screenshot、Evidence、Report は Editor Area の Webview Panel で表示できるようにする。通常表示では Locator JSON、Digest、Stable Key、Raw DOM、SQL、Secret、MCP Raw I/O を表示しない。

Webview と外部 Browser 版は同じ REST API、Read Model、Confirmation API、Stage、Plan Revision、Artifact Digest を使用する。片方だけに存在する業務機能や、別々の状態管理を作らない。外部 Browser 版は完全に削除せず、全画面表示、障害診断、VS Code が利用できない場合の回復入口として残す。

Webview は VS Code の Content Security Policy、Nonce、Resource URI、HTML Escape を使用する。任意 Script、Inline Event Handler、未確認 Local File、Secret を Webview へ渡さない。API 接続先は Runtime Manifest から取得した Loopback URL に限定する。

## MCP の統合

現在の Python MCP Tool 実装を Domain Service から切り離し、Local Backend の MCP Streamable HTTP Endpoint から提供する。

```text
POST http://127.0.0.1:<port>/mcp
Authorization: Bearer <Bridge Token>
```

VS Code Extension は `McpHttpServerDefinition` を登録し、Copilot が次の五 Tool だけを利用できるようにする。

- `copilot_get_coding_task`
- `copilot_record_change_outputs`
- `copilot_run_task_command`
- `copilot_validate_task_diff`
- `copilot_record_task_result`

MCP の JSON-RPC／Transport処理と既存 Tool Dispatch を分離し、stdio と HTTP の二つの Domain 実装を作らない。移行中だけ同じ Tool Service に対するstdio互換を維持し、HTTPの実検証後に `OperaMindMcp.exe` とRuntime ManifestのMCP commandを削除する。

MCP Endpoint はLoopbackだけで公開し、Bridge Tokenを必須とする。Tokenはユーザー領域で生成し、VS Code SecretStorageへ同期する。TokenをURL、Query、ログ、Evidence、Copilot Context、Webview Stateへ保存しない。Origin制限、Request Size、Tool Call上限、Timeout、Idempotency、Workspace／Task Bindingは現在のfail-closed規則を維持する。

## Local Backend の起動

利用者に `migrate`、`web`、`mcp` を個別実行させない。

初回InstallまたはLauncher起動時に、ユーザー領域へVersion付きRuntime Manifest、実行ファイルPath、Web URL、Bridge Token Fileを保存する。Extensionは起動時にManifestを読み、次の順でBackendを確認する。

1. Runtime ManifestのSchema、Product、Version、絶対Pathを検証する。
2. Loopback `/health` のProductとVersionを確認する。
3. 停止中の場合だけ、確認済みInstall PathのBackendを固定引数で起動する。
4. 別ProductがPortを使用している場合は起動せず、明確な修復案を表示する。
5. Migration、Web、MCP HTTPがReadyになってからWebviewとMCP Definitionを有効化する。

任意Shell、Workspace内のExecutable、PATH検索結果、Webview入力からBackendを起動しない。二重起動を防止し、起動したProcessのVersionと配布VSIXのVersionが一致しない場合はTaskを開始しない。

BackendをVS Code終了時に必ず強制終了する設計にはしない。実行中のCoordinator、Playwright、Cleanup、Evidence保存を確認し、安全なIdle状態だけを停止対象にする。終了方針は実装開始時にProcess LifecycleとRecovery Contractとして固定する。

## Runtime Manifest

HTTP MCP移行後のRuntime Manifestは、少なくとも次だけを公開する。

```json
{
  "schemaVersion": 2,
  "product": "operamind",
  "version": "<single-release-version>",
  "webUrl": "http://127.0.0.1:<port>",
  "backendExecutable": "<absolute-installed-path>",
  "bridgeTokenFile": "<absolute-user-data-path>"
}
```

MCPのcommand、args、cwdは削除する。旧Schema v1は移行期間だけ読み取り、v2生成後はv1へ戻さない。Manifest、Desktop、Backend、VSIX、ContractのVersionは一つのVersion Sourceから生成する。

## 配布物

Windows:

```text
OperaMind.exe
operamind-copilot-bridge.vsix
```

macOS:

```text
OperaMind.app
operamind-copilot-bridge.vsix
```

`OperaMindMcp.exe`はHTTP MCPの実受入テスト完了後に削除する。VSIXへPython Runtime、PostgreSQL Driver、Playwright Browser、Domain Service全体を埋め込まない。Backendは現在と同じくPython Runtimeを内包した配布物とする。

## 実装開始時の順序

1. Desktop、MCP、VSIXのVersion Sourceを統一する。
2. MCP Tool Dispatchをstdio Transportから分離する。
3. Token保護されたLoopback MCP HTTP Endpointを追加する。
4. Extensionを`McpHttpServerDefinition`へ移行する。
5. WebのAPI Clientと表示ModuleをWebviewで再利用可能にする。
6. Activity Bar要約とEditor Area詳細画面を追加する。
7. Runtime Manifest v2とBackend自動起動／Recoveryを追加する。
8. 外部BrowserとWebviewの同一Confirmation／Read Modelを検証する。
9. Windows／macOSの配布物から`OperaMindMcp`を削除する。
10. Packaging、Configuration、Upgrade、Rollback、手動E2E手順を更新する。

## 完了条件

- VSIXだけでOperaMind MCPが自動登録され、利用者がMCP commandを設定しない。
- `OperaMindMcp.exe`なしで五つのMCP Toolが動作する。
- VS Code WebviewからProject初期化、変更要件、確認、TestPlan、UI Test、Evidence、Reportを操作できる。
- Webviewと外部Browserが同じStage、Plan Revision、Artifact Digest、Confirmationを表示する。
- Backend停止、Port競合、Token不一致、Version不一致を明確にblocked表示する。
- VS Code再起動後にBackend、Webview、MCP、現在Taskを安全に復旧する。
- Secret、Token、SQL、Raw DOMがWebview、ログ、Evidence、Copilot Contextへ漏れない。
- Windows NativeとmacOSでPackage Smoke Test、実PostgreSQL、実VS Code GitHub Copilot、実Playwright閉ループを完了する。
- Fake、推測、静かなfallbackを完成Evidenceとして使用しない。

