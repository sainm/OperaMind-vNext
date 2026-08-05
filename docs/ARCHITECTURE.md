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

VS Code GitHub Copilot に対して、対象変更に結び付いた限定タスクを配信する。Bridge Token は VS Code SecretStorage に保持し、平文を DB、ログ、画面、Copilot Context、Evidence に保存しない。

### Document RAG

Requirement から関連 Document ID を検索し、Canonical DB で本文と関係を復元する。Index 未準備、Profile drift、Project 漏えいがある場合は `document_change` または `code_scope` を停止する。

Project Onboarding は Web Request 内で Embedding を実行しない。Project と Git 基線を先に永続化し、PostgreSQL の `project_onboarding_runs` を `discover`、`learn`、`documents`、`index` の順に一段ずつ進める。`learn` は Project Workspace に固定した Bridge／MCP Task として VS Code 上の GitHub Copilot へ渡し、草案をサーバー側で全実 Sample に再適用する。Coverage 100%／曖昧 0 件の草案だけを Web で確認でき、確認済み Run に結び付いた Profile Version Set だけを後続 Canonical 化が使用する。別 Project や過去 Run の Binding は検索候補に混ぜない。内容値は構造 Digest から除外し、Sheet、Heading、Header、File Set の変化だけを再学習条件にする。Canonical DB、Profile Version、監査記録は更新し、RAG Index は確認済み Snapshot から再構築する。

### Code Graph / Impact

固定 Git Revision を解析し、変更候補ファイル、Symbol、Route、Test Binding を生成する。未解決 Edge は停止理由として残し、AI の推測で閉じない。

### Test Runtime

Copilot が同一 content digest に対する必須コンパイル／テスト／Coverage と committed EditResult の成功後に作った実ブラウザ用 UiTestPlan / TestDataPlan を受け取る。OperaMind は全 Business Rule を、実行可能 UI Test、検証済み Code Test、または現在 Task に実在する Command／Canonical Artifact／Plan Component Evidence に照合してカバレッジを再計算し、100% 未満なら未カバー Rule を同じ Copilot `test_planning` Task に返して完全版を再生成させる。Evidence は型に対応する許可済み参照へ解決できなければ採用しない。100% 未満の Plan は永続化、人工確認、Test Data 実行、UI Test に進めない。標準実行器は Project 固有 `test_base_url` に限定した HTTP / Playwright UI と、Project の確認済み Target Data Profile に限定した PostgreSQL SQL Binding を提供する。対象 DB Secret はユーザー領域だけに保存し、Canonical DB と Copilot Context には Binding ID、入力制約、readback／cleanup／冪等 metadata と Identity Contract だけを保存・公開する。

Database は二つの境界に分ける。OperaMind 自身の Canonical Data、RAG、監査、Lease は pgvector を使用するため PostgreSQL 固定であり、被テストシステムの DB 方言とは連動させない。被テストシステム DB は `TargetDatabaseAdapter` と明示的な Dialect Registry の境界で分離する。Profile、SecretStore、SQL 実行器は Adapter key を共有し、未登録 key は保存／計画確認／実行の各入口で fail closed となる。現在 Registry に登録する production 実装は、`psycopg`、PostgreSQL named parameter、`information_schema`／`pg_catalog` 検査を使う `PostgresqlTargetDatabaseAdapter` だけであり、Oracle DSN や Oracle SQL を受理しない。将来 Oracle を追加するときは、Secret parser、named bind、metadata／constraint 検査、型変換、transaction、readback、error sanitizer を一つの Adapter として実装して Registry に登録する。Control DB の新 migration や主フロー分岐は不要だが、実 Oracle 回帰が通るまで Web の設定可能値に `oracle` を追加しない。

Test Data Coverage は Business Coverage と別の実行時 Gate である。確定した AcceptanceCriteria、TestCase、`test_data_id` の全組合せを母数とし、TestDataPlan の `coverage_conditions` が不足、範囲外、重複、Identity Key だけの存在確認、未確認列参照であれば受理しない。実行時は各 `test_data_id` を登録済みの実 `DataIdentityProvider`（database／api／ui／hybrid）へ結び、実観測から `primary_key`、`business_unique_keys`、`screen_identity_values`、`record_scope_locator`、`match_count=1`、`evidence_ref` を同一形式で固定する。Hybrid は各 Source の同名・同値 Identity による連結を必須とし、全 Source が同一業務レコードを観測したことを検証する。未登録 Provider、必要な source Evidence の欠落、fake、推測、静かな fallback は blocked とする。業務項目、状態、境界値、関連関係の Coverage は別途確認済み SQL readback から評価する。期待値、実測値、判定、digest、`data_coverage` Evidence を Run に固定し、エンジン算出値が 100% になる前に TestPlan の UI Step を開始しない。Coverage の分母、実測値、成功判定を Copilot の自己申告から取得しない。

全 UI Step は `operation_scope=screen|bound_record` を必須とし、跨画面および表 UI は `bound_record` と `data_binding_ref` を持つ場合だけ固定画面キーから展開した exact Locator を唯一の Scope として実行する。独立した Browser 予行 Run は作らず、正式 Run の同一 Browser Context で状態変更 Action ごとに期待値付き `pre_action_observations` を読み取る。操作直前に Origin、Action count=1、確認済み画面状態を検証し、Binding Step ではさらに Scope count=1 と現在 Project／Run／digest を確認し、同じ container の実 DOM から全業務一意キーと全画面キーを読み、正規化した値から `observed_identity_digest` を計算して frozen identity digest と比較する。Frozen Binding の期待 digest／content digest を DOM 観測結果として受理しない。分類欠落・矛盾、DB／画面の 0 件、複数件、DOM 身元不一致、drift を操作前に fail closed にする。行番号、曖昧 Text、AI 推測、固定済み Scope の computer-use fallback は禁止する。Locator block は Screenshot、Step Log、失敗工程、match count を Evidence にして同じ Copilot Change Task へ返し、別の Plan Revision と Run だけで再試行する。任意 SQL、未登録 Binding、Schema／Column drift、型・長さ・必須・列挙・業務制約違反、read-after-write 不一致、cleanup 不在は確認または実行時に fail closed となる。Fixture は test injection 専用で production には登録しない。Cleanup は失敗時も試行する。実行権は PostgreSQL の単一 Owner Lease と Heartbeat で排他し、期限内の二重実行を拒否する。Lease 切れまたは試行上限到達時は同じ Test Data を推測再実行せず、実行中断を永続化して人工判断可能な fail-closed 状態へ回復する。UI Step ごとのサニタイズ済み Screenshot と Step Log を保存し、実行結果が揃った場合だけ `UiVerificationResult` と最終 Closure Report を自動生成する。Web の自然言語修正は read-only Copilot revision Task を経由し、同じ Automation Run の上流確認を維持したまま TestPlan 以降だけ再確認・再実行し、旧 Run／Evidence／Closure／Report を stale として無効化する。

`RunContext` は一回の実行に対する正式な共有境界であり、`runtime_variables`、`frozen_data_bindings`、`flow_dependencies`、`evidence_refs` を持つ。Flow は明示した DAG の順でだけ実行し、存在しない依存と循環依存は開始前に blocked となる。Flow 局所変数は Flow 外へ暗黙に漏らさず、Run 共有値は読み取り専用とする。`operamind_run_id`、`test_data_token`、`execution_started_at` は Run 作成時に一度生成して凍結し、同じ Run の全 Test Case／Flow が参照できるが上書きできない。

既存データは Project の確認済み `DataIdentityProvider` を実行して一意候補を作り、人工確認後だけ `binding_mode=adopted` の計画データになる。Run 中に解決した `TestDataBinding` は `project_id`、`run_id`、`test_data_id`、identity／content digest で固定し、同一 Run の後続 Flow と別 Test Caseから参照できる。一度凍結した Binding の更新・上書き、別 Run／別 Project の参照、digest 不一致は禁止する。

Cleanup も setup／業務 Step と同じ frozen Binding を参照する。UI Cleanup は frozen record scope を 1 件確認し、同じ DOM container の実業務キーから digest を再計算した後、その container 内だけを操作する。操作後の UI match count は 0 でなければならず、Database／API Provider がある場合は同 Source でも不存在を確認する。Step、Scenario、Screenshot は実際に使用した Binding ref を持ち、Closure v3 は `業務要件 → Test Case → UI Step → TestDataBinding → Provider → 実レコード → Assertion／Screenshot／Cleanup` の追跡を生成する。参照不明、Scope 越境、digest 不一致を成功 Evidence へ変換しない。

### Closure

要件、StructuredChange、ImpactReport、EditResult、Command Evidence、TestData、Business Coverage、UI Result を結合し、欠落があれば fail closed にする。
