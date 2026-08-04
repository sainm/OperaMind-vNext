# OperaMind vNext アーキテクチャ

## 境界

OperaMind はローカルの変更デリバリー制御面であり、AI コードエディターではない。設計書、コード、テスト計画の編集は VS Code GitHub Copilot が行い、OperaMind は限定コンテキスト、検証、実行、Evidence を提供する。

```text
OperaMind Web
  │  ChangeRequest / MainChangeFlow
  ▼
Application Flow
  ├─ Document RAG ── Canonical Documents / Search Index
  ├─ Code Scope ──── Code Graph / Impact Report
  ├─ Copilot Bridge ─ VS Code GitHub Copilot
  ├─ Test Runtime ─── Test Data → bounded UI steps / Playwright → Screenshot Evidence
  └─ Closure ──────── Coverage / Evidence / Final Report
```

## 信頼境界

- PostgreSQL の正規化データと不変 Artifact が Canonical Data である。
- Vector Index は検索用であり、正本ではない。
- Git Revision、Document Snapshot、Profile Version、Deployment を Evidence に固定する。
- AI 出力は候補であり、Schema、範囲、Revision、差分、コマンド結果を検証してから Canonical 化する。
- Web は内部の承認・Queue・Lease を表示せず、単一の `ChangeFlowStateMachine` が六工程の状態に投影する。MCP、Copilot Bridge、Coordinator も同じ状態解釈を使用する。
- Coordinator は全 Project／Change Request を走査せず、DB から自動実行待ち、実行中復旧、期限切れ Lease の Run だけを有界取得する。

## コンポーネント

### Web

変更要件一覧、六工程、各工程の成果物、停止理由、最終レポートだけを表示する。管理コンソールや Worker 操作画面は持たない。

### Local Bridge / MCP

VS Code GitHub Copilot に対して、対象変更に結び付いた限定タスクを配信する。Bridge Token は VS Code SecretStorage に保持し、平文を DB や画面に保存しない。

### Document RAG

Requirement から関連 Document ID を検索し、Canonical DB で本文と関係を復元する。Index 未準備、Profile drift、Project 漏えいがある場合は `document_change` または `code_scope` を停止する。

Project Onboarding は Web Request 内で Embedding を実行しない。Project と Git 基線を先に永続化し、PostgreSQL の `project_onboarding_runs` を `discover`、`learn`、`documents`、`index` の順に一段ずつ進める。`learn` は Project Workspace に固定した Bridge／MCP Task として VS Code 上の GitHub Copilot へ渡し、草案をサーバー側で全実 Sample に再適用する。Coverage 100%／曖昧 0 件の草案だけを Web で確認でき、確認済み Run に結び付いた Profile Version Set だけを後続 Canonical 化が使用する。別 Project や過去 Run の Binding は検索候補に混ぜない。内容値は構造 Digest から除外し、Sheet、Heading、Header、File Set の変化だけを再学習条件にする。Canonical DB、Profile Version、監査記録は更新し、RAG Index は確認済み Snapshot から再構築する。

### Code Graph / Impact

固定 Git Revision を解析し、変更候補ファイル、Symbol、Route、Test Binding を生成する。未解決 Edge は停止理由として残し、AI の推測で閉じない。

### Test Runtime

Copilot が同一 content digest に対する必須コンパイル／テスト／Coverage と committed EditResult の成功後に作った実ブラウザ用 UiTestPlan / TestDataPlan を受け取る。OperaMind は全 Business Rule を、実行可能 UI Test、検証済み Code Test、または現在 Task に実在する Command／Canonical Artifact／Plan Component Evidence に照合してカバレッジを再計算し、100% 未満なら未カバー Rule を同じ Copilot `test_planning` Task に返して完全版を再生成させる。Evidence は型に対応する許可済み参照へ解決できなければ採用しない。100% 未満の Plan は永続化、人工確認、Test Data 実行、UI Test に進めない。標準実行器は Project 固有 `test_base_url` に限定した HTTP / Playwright UI と、Project の確認済み Target Data Profile に限定した PostgreSQL SQL Binding を提供する。対象 DB Secret はユーザー領域だけに保存し、Canonical DB と Copilot Context には Binding ID、入力制約、readback／cleanup／冪等 metadata と Identity Contract だけを保存・公開する。

Test Data Coverage は Business Coverage と別の実行時 Gate である。確定した AcceptanceCriteria、TestCase、`test_data_id` の全組合せを母数とし、TestDataPlan の `coverage_conditions` が不足、範囲外、重複、Identity Key だけの存在確認、未確認列参照であれば受理しない。実行時は各 `test_data_id` を実 DB の主キー、業務一意キー、画面識別キーへ 1 件だけ結び、同じ確認済み SQL readback から業務項目、状態、境界値、関連関係を評価する。期待値、実測値、判定、digest、`data_coverage` Evidence を Run に固定し、エンジン算出値が 100% になる前に TestPlan の UI Step を開始しない。Coverage の分母、実測値、成功判定を Copilot の自己申告から取得しない。

全 UI Step は `operation_scope=screen|bound_record` を必須とし、跨画面および表 UI は `bound_record` と `data_binding_ref` を持つ場合だけ固定画面キーから展開した exact Locator を唯一の Scope として実行する。分類欠落・矛盾、DB／画面の 0 件、複数件、drift を fail closed にする。行番号、曖昧 Text、AI 推測、固定済み Scope の computer-use fallback は禁止する。任意 SQL、未登録 Binding、Schema／Column drift、型・長さ・必須・列挙・業務制約違反、read-after-write 不一致、cleanup 不在は確認または実行時に fail closed となる。Fixture は test injection 専用で production には登録しない。Cleanup は失敗時も試行する。実行権は PostgreSQL の単一 Owner Lease と Heartbeat で排他し、期限内の二重実行を拒否する。Lease 切れまたは試行上限到達時は同じ Test Data を推測再実行せず、実行中断を永続化して人工判断可能な fail-closed 状態へ回復する。UI Step ごとのサニタイズ済み Screenshot と Step Log を保存し、実行結果が揃った場合だけ `UiVerificationResult` と最終 Closure Report を自動生成する。Web の自然言語修正は read-only Copilot revision Task を経由し、同じ Automation Run の上流確認を維持したまま TestPlan 以降だけ再確認・再実行し、旧 Run／Evidence／Closure／Report を stale として無効化する。

### Closure

要件、StructuredChange、ImpactReport、EditResult、Command Evidence、TestData、Business Coverage、UI Result を結合し、欠落があれば fail closed にする。
