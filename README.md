# OperaMind vNext

OperaMind は、ローカルの変更要件を起点に、設計書、コード、テスト、UI 検証を一つの流れで完了させる変更デリバリー支援ツールです。

AI による編集は **VS Code 上の GitHub Copilot** に統一します。OperaMind 自身はコードや設計書を生成せず、RAG、Code Graph、実行範囲、テストデータ、Playwright、Evidence を管理します。

![OperaMind の六工程変更閉ループ](docs/assets/operamind-main-flow.svg)

## 主フロー

1. Web で変更要件を登録する。
2. RAG が関連片を検索し、`document_id` から完全な Canonical 文書と実ファイル参照を復元して、VS Code GitHub Copilot が対象設計書を修正する。
3. VS Code GitHub Copilot が設計書差分からコード候補を調査し、OperaMind が現在の Code Graph、Repository、Revision、Test Binding に照合して変更可能範囲を確定する。
4. VS Code GitHub Copilot が限定範囲のコードを変更し、コンパイル・テスト・カバレッジ成功後に実ブラウザ用 UiTestPlan / TestDataPlan を生成する。生成済み計画は Web から自然言語で修正を提案でき、確認後は Copilot が完全な UI 計画を再生成してから新しい実行を開始する。
5. OperaMind が TestDataPlan の順序で一連の業務データを生成し、その後に UI 操作・断言・スクリーンショット取得を実行する。
6. 要件、設計書差分、コード範囲、テスト結果、UI Evidence を結合した最終レポートを出力する。

Web が表示するのはこの六工程だけです。内部の承認記録、実行キュー、Lease、Worker、再試行制御は自動処理され、通常画面には表示しません。
画面は六工程を読み取るだけで、画面の表示や更新を処理継続の条件にしません。Copilot の成果物を受信した後は、内部 Coordinator が TestDataPlan、UI 検証、最終レポートまで自動で進めます。

## 役割

| コンポーネント | 責務 |
|---|---|
| OperaMind Web | 変更要件と六工程の成果物・状態を表示し、生成済みテストケースの自然言語修正を提案・確認 |
| VS Code GitHub Copilot | 設計書変更、コード変更、UiTestPlan / TestDataPlan 生成・再生成 |
| Local Bridge / MCP | Copilot に限定コンテキストと安全な実行ツールを提供 |
| RAG / Code Graph | 設計書検索、Canonical Data 復元、コード影響範囲確定 |
| Test Data / Playwright | 画面横断データ生成、UI 操作、スクリーンショット取得 |
| Evidence / Closure | 結果検証、カバレッジ、最終レポート |

Copilot に公開する MCP は、Change Task の取得、成果物記録、コマンド実行、差分検証、最終結果記録の五つだけです。Impact、Approval、実行キューなどの内部機構を個別 Tool として公開しません。

## 起動

配布版では、macOS の `OperaMind.app` または Windows の `OperaMind.exe` を起動するだけです。Launcher が設定読込、Bridge Token 生成、DB migration、Web 起動、ブラウザー表示を一度に行います。MCP は VS Code Extension がユーザー領域の実行情報から自動登録するため、利用者が `migrate`、`web`、`mcp` を個別に実行する必要はありません。

以下は配布物を作る開発者だけが使用するソース起動手順です。

Python 3.12 と PostgreSQL 18 + pgvector を使用します。

macOS／Linux:

```bash
python3.12 -m venv .venv
.venv/bin/pip install -e '.[dev]'

cp .env.example .env
.venv/bin/operamind-launcher --root .
```

Windows PowerShell:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"

Copy-Item .env.example .env
# .env の OPERAMIND_DATABASE_URL を Windows PostgreSQL の URL に変更する
.\.venv\Scripts\operamind-launcher.exe --root .
```

本機の固定開発 DB 名は `operamind_vnext` です。ソース実行では接続先、Embedding、UI Test の設定をリポジトリ Root の `.env` に置き、実値は Git に追加しません。配布版はユーザー領域の `config.env` を読み込みます。OS の既存環境変数、ユーザー設定、ソース `.env` の順で優先されます。VS Code MCP は Launcher と同じ実行環境を使用するため、Database URL の入力 Dialog は表示されません。

必須／任意の全設定、OS 別 Path、設定値の確認方法、Project 画面の入力、Embedding、Playwright、Target Data SQL Binding、Secret の保存先、障害時の確認方法は [設定ガイド](docs/CONFIGURATION.md) にまとめています。`.env` と `config.env` は端末全体の実行設定、Workspace・設計書・UI URL・被テスト DB は Project ごとの画面設定です。

Windows 配布版の設定は `%LOCALAPPDATA%\OperaMind\config.env`、実行情報は `%LOCALAPPDATA%\OperaMind\runtime.json` に固定します。`config.env` は UTF-8、UTF-8 BOM、CRLF のいずれも受け付けます。Windows の PostgreSQL は Unix socket 形式ではなく、`postgresql://operamind:<PASSWORD>@127.0.0.1:5432/operamind_vnext` のような TCP URL を使用します。Token と runtime 情報は Launcher が生成するため、利用者が編集しません。

Web は単機利用を前提とし、`127.0.0.1` のみで公開します。ユーザー認証はありません。Bridge Token は Launcher がユーザー領域へ生成し、VS Code Extension が SecretStorage へ同期します。

初回は Web の「新しいプロジェクト」から、コード Workspace、一つ以上の設計書 Folder、Project 固有の UI テスト対象 URL を登録します。OperaMind はコードと各設計書 Folder の実際の Git Repository Root を判定します。Git 管理外の Folder は外部送信しない内部 Git Repository と初回 Commit を作成し、既存 Repository は未 Commit 変更がない場合だけ現在 Commit を基線にします。設計書 Folder がコード Repository 内にある場合は同じ Repository を再利用し、Nested Repository は作成しません。Windows では Windows の絶対 Path、macOS／Linux では各 OS の絶対 Path を入力します。

Project 登録後の Onboarding はバックグラウンドで `構造抽出 → 設計書学習 → Canonical 文書 → RAG 索引` を順番に実行します。Project ごとの XLSX／DOCX から Sheet、Heading、Header と Sample を抽出し、VS Code 上の GitHub Copilot が Project 専用 `DocumentConventionProfile` 草案を生成します。Web の「設計書学習」で Field Mapping、Stable Key、曖昧点、Sample Coverage を確認し、Coverage 100%／曖昧 0 件の場合だけ Version を適用します。別 Project の学習結果は流用しません。内容だけが変わった場合は現行 Profile で Canonical／RAG を更新し、Sheet、Heading、Header 等の構造が変わった場合は差分学習と再確認を要求します。PostgreSQL の Canonical Data、Profile Version、監査記録は更新可能で、RAG は確認済み Version から自動再構築します。

TestDataPlan で対象システムの HTTP／UI Step を実行する場合は、Project 初期化画面で資格情報を含まない Origin を明示します。SQL を使う Project は同じ画面の「被テストシステム DB データ準備」で Database 方言、接続 Alias、確認済み Query Binding を登録します。被テスト DB は `TargetDatabaseAdapter` Registry で分離し、現在は実 PostgreSQL Adapter だけを登録します。未登録方言は blocked とし、別 DB へ fallback しません。接続 Secret はユーザー領域の owner-only SecretStore に保存し、OperaMind DB、ログ、Copilot Context、Evidence へ保存・送信しません。Copilot には `query_binding_id`、入力制約、cleanup 関係と確認済み Identity Contract だけを渡し、任意 SQL は受理しません。各 Test Data は登録済みの実 `DataIdentityProvider`（`database`／`api`／`ui`／`hybrid`）で実観測を解決し、`primary_key`、`business_unique_keys`、`screen_identity_values`、`record_scope_locator`、`match_count`、`evidence_ref` の同一契約へ固定します。Hybrid は各 Source に共通する同名・同値の Identity を必須とし、すべての Source が同じ業務レコードを指すことを検証します。`match_count` は必ず 1 であり、Provider 未登録、source Evidence 欠落、fake、推測、静かな fallback は Plan または実行を blocked にします。`bound_record` 操作の直前には exact Scope が 1 件であることを確認し、その実 DOM から業務一意キーと画面識別値を読み、OperaMind 自身が `observed_identity_digest` を計算して frozen identity digest と比較します。期待 digest や Binding の content digest を観測値として流用できません。全 UI Step は `operation_scope` を `screen` または `bound_record` として明示し、後続の跨画面・表操作は `bound_record` と `data_binding_ref` を必須として、固定した画面キーの exact Locator 内だけで実行します。0 件、複数件、DOM 身元不一致、画面 drift を検出した場合は操作前に停止します。行番号、曖昧 Text、AI 推測、固定済み行に対する computer-use fallback は使用しません。各 write Binding は項目型、長さ、必須、列挙・業務制約、実 Table／Column 検査、read-after-write、cleanup、Transaction、冪等方針を満たさなければ Plan 確認前に fail closed になります。Fixture は自動テスト用の注入 Adapter に限定し、production の対象システムデータ準備には使用しません。

既存の実データは日本語画面「既存テストデータを登録」から、データ名、業務上の一意値、Test Case、保持方針だけで接管できます。登録は選択中の Change Request に隔離され、確認済み Provider が一件だけを実観測した候補を人工確認すると、Copilot に完全な Plan Revision を自動依頼します。Schema、安全性、Business Coverage 100%、Test Data Coverage 条件の静的対応と最終確認を通過した後にだけ正式な `binding_mode=adopted` TestDataPlan となり、旧 Plan は実行できません。実 Run では Test Data Coverage を実 Observation から計算し、100% に達した後だけ UI Step へ進みます。Run ごとの `RunContext` は読み取り専用 Token と凍結 Binding を保持し、後続 Flow／別 Test Case／Cleanup が同じ実レコードを参照します。「固定データ識別子」では Copilot 待ち、最終確認待ち、計画済みを区別し、実行後は業務値、利用箇所、Evidence、Cleanup を確認できます。普通利用者には SQL、Locator、主キー、Secret、内部 Binding ID を表示しません。詳細設定は [設定ガイド](docs/CONFIGURATION.md) を参照してください。

データを「生成した」ことと、テストを「データでカバーした」ことは別に判定します。OperaMind は確定した AcceptanceCriteria、TestCase、`test_data_id` の全組合せを母数にし、TestDataPlan の `coverage_conditions` と完全一致しなければ Plan を受理しません。各条件は同じ DataIdentityProvider の確認済み実 Observation を参照し、database は SQL readback、api は HTTP response、ui は Playwright Observation、hybrid は明示した実 Source から項目値、状態、境界値、関連関係を再評価します。期待値、実測値、Observation Source、結果、digest、Evidence を Run に保存し、OperaMind が算出した Test Data Coverage が 100% になるまで TestPlan の UI Step、Screenshot、人工確認後の UI 検証へ進みません。AI が Coverage 値や成功結果を自己申告することはできません。

## VS Code

```bash
cd vscode-extension
npm ci
npm run package:vsix
```

生成した `dist/operamind-copilot-bridge.vsix` を VS Code にインストールします。以後は対象 Workspace を開くだけで、Extension が OperaMind MCP と Bridge Token を自動検出します。詳細は [VS Code GitHub Copilot ワークフロー](docs/VSCODE-COPILOT-WORKFLOW.md) を参照してください。

macOS／Windows の配布物は GitHub Actions の `Package desktop` から作成します。各 ZIP には OperaMind Desktop と同じ Version の VSIX が入り、Desktop は Python を内包します。Windows ZIP の `OperaMindMcp.exe` は VS Code が標準入出力用に自動起動する内部 Companion であり、利用者が直接実行するものではありません。`OperaMind.exe` と同じ Folder から移動・削除しないでください。PyInstaller は OS 間の cross-build を行わないため、macOS App と Windows exe は各 OS の Runner で個別に検証します。

## 品質確認

```bash
.venv/bin/python -m ruff check .
.venv/bin/python -m mypy
.venv/bin/python -m pytest -q
```

Windows PowerShell:

```powershell
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m mypy
.\.venv\Scripts\python.exe -m pytest -q
```

PostgreSQL 統合テストでは、管理用接続だけを `OPERAMIND_TEST_DATABASE_URL` に設定します。pytest がランダム名の一時 DB を作成・migration・削除し、通常の開発 DB を再利用しません。

## 設計文書

- [設定ガイド](docs/CONFIGURATION.md)
- [再構成方針](docs/RECONSTRUCTION.md)
- [全体アーキテクチャ](docs/ARCHITECTURE.md)
- [MVP スコープ](docs/MVP-SCOPE.md)
- [Canonical Data Model](docs/CANONICAL-DATA-MODEL.md)
- [実 RAG](docs/P2-REAL-RAG.md)
- [Code Graph](docs/P3-CODE-GRAPH.md)
- [Impact Analysis](docs/P4-IMPACT.md)
- [UI Verification](docs/P5-UI-VERIFICATION.md)
- [汎用手動 E2E テスト手順](docs/MANUAL-E2E-TEST.md)
- [品質ベースライン](docs/QUALITY-BASELINE.md)
