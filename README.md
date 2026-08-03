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

Windows 配布版の設定は `%LOCALAPPDATA%\OperaMind\config.env`、実行情報は `%LOCALAPPDATA%\OperaMind\runtime.json` に固定します。`config.env` は UTF-8、UTF-8 BOM、CRLF のいずれも受け付けます。Windows の PostgreSQL は Unix socket 形式ではなく、`postgresql://operamind:<PASSWORD>@127.0.0.1:5432/operamind_vnext` のような TCP URL を使用します。Token と runtime 情報は Launcher が生成するため、利用者が編集しません。

Web は単機利用を前提とし、`127.0.0.1` のみで公開します。ユーザー認証はありません。Bridge Token は Launcher がユーザー領域へ生成し、VS Code Extension が SecretStorage へ同期します。

初回は Web の「新しいプロジェクト」から、コード Workspace、一つ以上の設計書 Folder、Project 固有の UI テスト対象 URL を登録します。OperaMind はコードと各設計書 Folder の実際の Git Repository Root を判定します。Git 管理外の Folder は外部送信しない内部 Git Repository と初回 Commit を作成し、既存 Repository は未 Commit 変更がない場合だけ現在 Commit を基線にします。設計書 Folder がコード Repository 内にある場合は同じ Repository を再利用し、Nested Repository は作成しません。Windows では Windows の絶対 Path、macOS／Linux では各 OS の絶対 Path を入力します。

TestDataPlan で対象システムの HTTP／UI Step を実行する場合は、Project 初期化画面で資格情報を含まない Origin を明示します。URL は Project と TestDataPlan 実行に固定され、未設定の場合は確認前に fail closed になります。

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
