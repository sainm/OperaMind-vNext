# OperaMind 主変更閉ループ再構成

## 目的

OperaMind の製品境界を、次の一つの変更閉ループに固定する。

```text
変更要件
  → RAG による設計書特定
  → VS Code GitHub Copilot による設計書変更
  → 設計書差分
  → RAG / Code Graph によるコード範囲
  → VS Code GitHub Copilot によるコード変更
  → コード差分検証
  → コンパイル・テスト・コード Coverage 検証
  → VS Code GitHub Copilot による TestPlan / TestDataPlan 生成
  → テストデータ生成
  → 有限 UI Step / Assertion 実行
  → 最終レポート
```

## 製品上の六工程

Web API と画面は、次の六つだけを公開する。

| ID | 表示 | 主担当 | 完了 Evidence |
|---|---|---|---|
| `requirement` | 変更要件 | 利用者 | Canonical ChangeRequest |
| `document_change` | 設計書差分 | VS Code GitHub Copilot | StructuredChange |
| `code_scope` | コード影響範囲 | OperaMind | ImpactReport |
| `compile_test` | コード変更・コンパイル・テスト | VS Code GitHub Copilot | committed EditResult と Command Evidence |
| `ui_validation` | テストデータ・UI 検証 | OperaMind | TestDataExecutionResult と UiVerificationResult |
| `final_report` | 最終レポート | OperaMind | ChangeClosureResult |

`GET /api/v1/change-requests/{id}/flow` が唯一の画面用進捗契約である。Approval Grant、Orchestration Task、Worker、Claim、Lease はこのレスポンスに含めない。

## AI 境界

- 編集 AI は VS Code 上の GitHub Copilot だけを使用する。
- Local Bridge / MCP は Canonical ID、対象ファイル、テストコマンド、差分検証、結果記録だけを渡す。
- OperaMind Web は AI チャットを実装しない。
- Embedding はローカル Provider を引き続き使用できる。
- 設計書本文、ソース全体、秘密情報を Web と Bridge のイベントへ複製しない。

## 内部機構

以下は必要な安全機構だが、製品工程ではない。

- 不変 Artifact と Evidence
- 範囲認可と idempotency
- Task Queue、Lease、Heartbeat、Retry
- Profile drift と再構築
- readiness gate

通常利用では自動実行し、失敗時は六工程の該当工程へ業務上理解できる停止理由だけを投影する。内部状態を操作する Web ページは作らない。

## 再構成状況

- [x] 六工程の `MainChangeFlow` 読み取りモデル
- [x] 六工程だけを表示する日本語 Web
- [x] 旧運用／設定／Task Graph／承認 UI の削除
- [x] 公開文書の重複整理
- [x] 六工程と必須成果物を持つ `CopilotCodingTask v2` / `copilot_change_task` 契約
- [x] 設計書生成用ファイル handoff を廃止し、文書結果の受領も同じ Change Task に統合
- [x] 同じ `copilot_record_change_outputs` を `document_change` → `code_scope` → `test_planning` の順で呼ぶ段階契約
- [x] コード差分検証後だけ TestPlan / TestDataPlan を受け付ける順序門禁
- [x] 自然言語 Requirement から current/ready Canonical Index を検索する文書候補 RAG
- [x] RAG の Section 候補を `document_id` で完全な Canonical 文書へ戻し、論理名と実文書参照を限定 Copilot Context に渡す
- [x] 登録済み Repository と現在 Git Revision による Analysis Case の自動作成
- [x] 要件、RAG 対象文書、文書差分、コード範囲、テスト計画、UI 実行、最終レポートを Web／VS Code 共通の Evidence Digest 付き人工確認へ統一
- [x] 人工確認後の RAG、実行範囲生成、テストデータ／UI 実行、Closure 評価を自動継続
- [x] Edit Packet、実行範囲、テスト実行許可の内部生成を自動化
- [x] TestDataPlan のデータ生成後に UI Step / Assertion / Screenshot を実行し、UI Result を自動生成
- [x] TestPlan、TestDataPlan の手順／変数／断言／cleanup と Closure 結果を六工程へ統合表示
- [x] 生成済み Test Case の自然言語修正を六工程内の「提案 → 差分／選択肢 → 一括確認」に限定し、確認後だけ TestPlan／TestDataPlan と下流実行を再生成
- [x] 自然言語 Test Step と Playwright UI Step を一対多で追跡し、生成 Step／跨画面変数／Assertion／cleanup の自然言語修正後も完全な Plan を再生成して対応漏れを阻断
- [x] Playwright DSL を主要な Web 操作、実 Frame URL を検証する同一 Origin iframe、追加 Observation まで拡張し、明示的 Capability Gap のみを確認済み AI Computer Use 境界へ渡す fail-closed Executor を追加。AI 後の URL、Observation、Screenshot は同じ Playwright Session から独立再取得する
- [ ] VS Code GitHub Copilot だけで AI Computer Use を実行する Visual MCP Provider を Extension に接続し、実端末 Evidence を取得
- [x] `コード影響範囲` に現在 Code Graph 由来の変更対象／依存／関連テストを SVG で表示し、Node 選択から Symbol、影響理由、関連テストを確認可能にする
- [x] Web 公開 API を Project、Change Request、六工程、六工程内 Test Case 修正、Screenshot、Local Bridge に限定
- [x] Web の Project／Change Request 一覧と作成応答から Case、Review、Copilot Task、Automation Run を除外
- [x] 旧 Draft / Checkpoint CLI と未使用 Application / Schema / テストを削除
- [x] 旧 Profile、UI Knowledge、Task 管理、手動 Impact / Grant の WebControlPlane 転送層を削除
- [x] 手動 Analysis Case bind CLI を削除し、Repository / Revision に基づく自動作成へ統一
- [x] 新 Web の PostgreSQL 統合回帰と in-app Browser 表示検証（1280px／390px、横方向 overflow なし）
- [x] Spring Boot 1.5／Thymeleaf／Gradle 用の Code Framework／Command Profile と Thymeleaf Route 解析
- [x] Gradle Wrapper、Spring Boot 1.5 設定、Thymeleaf 依存関係と Template を根拠に対象 Stack を自動判定
- [x] Stack が確定し Profile Binding が未設定の場合だけ Code Framework／Command Profile を内部で自動登録
- [x] Copilot Change Task に対象 Stack と固定 compile／test／build コマンドを付与（公開 Flow には内部連携情報を返さない）
- [x] Project Stack 判定、六工程 Read Model、公開 Change Request／Project Router を逐ファイル 80% coverage gate に追加
- [x] Project 初期化を Profile 駆動 XLSX／DOCX Onboarding に変更し、設計書識別・Canonical 文書・RAG 索引を PostgreSQL Lease 付きバックグラウンド Stage として保存。設定更新、再スキャン、再索引、Preflight、失敗 Stage 再試行を Web に追加
- [x] Copilot 可視 MCP を統一 Change Task 用の五 Tool に限定し、旧 Case／Impact／Grant／Packet／UI 直接 Tool を削除
- [x] Web の `flow/progress` 変更 API を削除し、TestDataPlan の予約・実行・Closure 更新を Web プロセス内の内部 Coordinator に移管
- [x] 自動化済み Approval／Impact／EditPacket／Orchestration／UI／Closure の旧手動 CLI 12 件と専用 CLI テストを削除（内部サービス、監査、readiness、復旧 Worker は維持）
- [x] 新 Change Task と競合する旧 dual-entry Change Loop CLI、停止済み monolithic P6 Executor、専用 Canonical Authorizer を削除
- [x] 呼び出し元と設定を失った汎用 Orchestration Worker CLI を削除し、Profile drift 復旧用の専用 Worker と共通安全実装だけを維持
- [x] Web の工程／成果物フィールドを六工程の allowlist に固定し、未知の内部フィールドを既定で非表示化
- [x] Copilot MCP の応答を共通 `stage_status` と工程別 `inputs`／`constraints`／`stage_contract` に統一し、Automation Run／Edit Packet／Approval／Task／Lease／Worker を非公開化
- [x] VS Code Copilot の起動 Prompt を現在工程の目的・入力・出力・停止条件に限定し、MCP の重複 `next_context` と完全 JSON テキスト出力を削除
- [x] `ChangeFlowStateMachine` を Automation 遷移、Web 六工程、MCP 確認待ち、Copilot 実行可否、Coordinator 自動実行の唯一の状態解釈にし、未知 Stage／Status と矛盾する終端状態を fail closed にする
- [x] Coordinator の全 Project／Change Request 走査を廃止し、自動実行待ち・実行中復旧・期限切れ Lease を DB の有界候補 Query と専用 Index で取得する
- [x] TestDataPlan 実行を PostgreSQL の Owner Lease／Heartbeat で排他し、期限切れまたは試行上限到達時はデータ操作を再実行せず中断結果へ fail closed に回復する
- [x] 五つの Copilot MCP Tool の Context／Command／Diff／Result 応答と Bridge 通知を allowlist 化し、Snapshot、Search Index、内部 Artifact、Authorization、Claim／Lease 情報を非公開化
- [x] 残存していた Dataset の documents／requirement dual-entry、手動 Analysis Start、StructuredChange Review、Code Scope CLI と専用 Planner／Batch／テスト／文書を削除
- [x] 旧 MCP／Web の公開先を失った Control Plane Case／Impact／UI Plan／Validation Query と UI Knowledge Review Query 層を削除
- [x] 主閉ループと接続されていない旧 UiKnowledge／BrowserManifest／UiVerificationPlan 実行管線を削除し、TestDataPlan 起点の限定 UI Evidence 経路へ統一
- [x] Copilot UiTestPlan／TestDataPlan を迂回する Frozen Golden Case の runtime fallback と旧 ChangeLoopCase 実行モデルを削除（Golden RAG はオフライン品質基準として維持）
- [x] 旧 UI Execution Plan 専用の Grant 認可、Web Validation 進捗 Query、二重 Screenshot origin を削除し、TestDataExecutionResult／ChangeClosureResult を唯一の UI 状態源に統一
- [x] 新規利用できない旧 `UiLocatorProfile` の Catalog／Schema／例と Browser Manifest 例を削除し、既存 DB の移行履歴と監査互換だけを維持
- [x] `UiVerificationResult v2` を current Orchestration／TestDataExecutionResult に固定し、Closure の UI 外部キーを `artifact_records` に移行
- [x] Readiness は同一 Orchestration の最新 TestData run のみ受理し、後続失敗・実行中 run がある場合は fail closed
- [x] `UiVerificationResult v2` に current Orchestration ID を固定し、Closure の旧 `change_validations` fallback を削除して Test Case Revision 間の Evidence 混入を fail closed に防止
- [x] Readiness、Profile Drift／Rebuild、Test Case Revision を current TestData／UI v2 Artifact に統一し、production source の旧 UI table query を全廃
- [x] target Origin を Project の `test_base_url` に固定し、別 Project や旧環境変数の URL を流用できないようにする
- [x] VisionDemo 固有の画面／API／業務日付／H2 Binding を持つ target adapter を削除し、跨画面 TestDataPlan だけを契約 fixture として維持。production は Copilot が直接生成した TestDataPlan、注入可能な `TestDataExecutorFactory`、fail-closed bounded executor に限定
- [x] VisionDemo 固有の Code／Relation／Command Profile を test fixture へ移し、production Profile は汎用 Schema と再利用可能な技術 Stack 例だけに限定
- [x] 旧 `CopilotHandoff` 内部名を `CopilotTaskContext` に統一し、MCP Server の公開説明から file handoff 表現を削除
- [x] 文書差分／Impact の未確認状態を完了表示せず、working `in_scope` と committed 成功を区別する六工程状態門禁
- [x] Command と被試験 content digest を固定し、committed EditResult と変更行 Coverage 成功後だけ実ブラウザ用 UiTestPlan／TestDataPlan を受理する
- [x] Web の自然言語 UI 計画修正を read-only Copilot revision Task として再生成し、旧 Run／Screenshot／Closure／Report を stale 化する
- [x] Copilot Impact と並存していた旧 deterministic Impact Report／Code Scope／Code Graph Query 管線を削除し、現行 Orchestration の Impact と graph artifact に統一
- [x] 内部 XLSX Proposal Writer／Workspace Editor を削除し、文書・コード変更は VS Code GitHub Copilot の限定 Change Task と受領差分だけに統一
- [x] production package 内の PostgreSQL テスト helper を `tests/support` へ移し、`BusinessDataTemplate` を廃止して跨画面データを直接 TestDataPlan へ統合
- [x] Project Target Data Profile を追加し、確認済み `query_binding_id` だけを SQL TestDataPlan に公開。対象 DB Secret はユーザー領域へ隔離し、型／長さ／必須／列挙・業務制約、実 Column、read-after-write、cleanup、Transaction、冪等方針を Plan 確認と production 実行の共通 Gate にした。Fixture は test injection 専用のまま維持
- [x] TestDataPlan の各 Test Data を実 DB 主キー／業務一意キー／画面識別キーへ一意に Binding し、Run 固有 digest と Evidence を固定。跨画面／表 UI を exact Binding Scope に限定し、0 件・複数件・drift・行番号・曖昧 Text・AI 推測を fail closed にした
- [x] AcceptanceCriteria／TestCase／TestData の全組合せを Test Data Coverage の母数に固定し、確認済み SQL readback の実測値で項目／状態／境界値／関連関係を検証。期待値・実測値・判定・digest・Evidence を永続化し、100% 未満では TestPlan UI Step を開始しない Gate を追加
- [x] 過去 source tree に結び付いた自動生成 UI／TestData／full-regression Evidence を削除し、再採取が必要な Readiness gate を pending へ戻す
- [x] 未登録だった `CopilotImpactContext` を Core Contract に追加し、Task Scheduler の旧 `UiExecutionPlan`／誤った `ChangeOrchestration` 出力型を現行五 Artifact に修正
- [x] テストだけが利用していた Application の Unresolved Evidence 再 export wrapper を削除し、実装と Repository の直接依存へ統一
- [ ] 対象 Spring Boot 工程の Repository パスを Project に登録し、Gradle Wrapper による実ビルド Evidence を採取
- [ ] 現在の Embedding Profile と実ローカル Provider を使った RAG 検索 Evidence を最終ソース Commit に再固定
- [ ] Microsoft Edge / Playwright live E2E と Screenshot Evidence を現在の対象 Deployment に再固定
- [ ] VS Code GitHub Copilot による一件の実変更閉ループを完走し、六工程と最終レポートを確認
- [ ] 最終ソース Commit と対象 Deployment に結び付いた zero-failure／zero-skip の `full_local_regression` Evidence を再生成
- [x] `local_files` Workspace に外部送信しない内部 Git 基線を作成し、前後 Diff、Code Scope、結果 Revision と Command Evidence を同じ方式で固定する
- [x] Project ごとの設計書構造を Copilot Task で学習し、100% Sample Coverage の確認済み Profile Version Set だけで Canonical／RAG を更新する
- [ ] Windows native で Web、MCP、Command 実行、Process tree 停止を統合確認し、POSIX 固有の実行 Path と Signal 前提を解消

未完了項目が残る間、この文書を「再構成完了」の証拠として扱わない。
