# 再現可能な品質ベースライン

このリポジトリの品質判定は、ローカルの印象や README の記述ではなく、固定されたコマンド、Coverage JSON、`quality/critical-coverage.json`、および検証済み readiness Evidence を正とします。

## 自動品質ゲート

GitHub Actions の `.github/workflows/quality.yml` は、Python 3.12、ロック済み依存関係、PostgreSQL 18 + pgvector 0.8.2 を使用し、次を実行します。

1. Ruff
2. Mypy strict
3. PostgreSQL 統合テストを含む Pytest
4. リポジトリ全体の statement coverage 80%
5. readiness／Golden RAG、Approval、Copilot／Project Stack、Main Change Flow、Project Onboarding、Target Test Data、Task scheduling、Recovery の各重要ファイル coverage は既定 80%。そのうち高リスク中核ファイルは個別に 90%

ローカルでは `CREATEDB` 権限を持つテスト用 Role の接続先を指定し、次のように同じゲートを再現します。指定した DB は管理接続としてのみ使用されます。Pytest は収集前に `template0` からランダム名の DB を作成し、全 migration を一度適用して `OPERAMIND_TEST_DATABASE_URL` を一時的に差し替え、終了時に残存接続を切断して DB を削除します。元の DB が汚れていても、その Schema やデータは複製も変更もされません。作成、migration、cleanup のいずれかが失敗した場合はテスト全体を fail closed にします。

```bash
export OPERAMIND_TEST_DATABASE_URL='postgresql://.../operamind_test'
.venv/bin/ruff check .
.venv/bin/mypy
.venv/bin/pytest -q \
  --ignore=tests/integration/test_live_embedding_provider.py \
  --ignore=tests/integration/test_golden_screen_change.py \
  --cov=operamind \
  --cov-report=term-missing \
  --cov-report=json:coverage.json \
  --cov-fail-under=80
.venv/bin/python scripts/check_critical_coverage.py
```

Windows PowerShell:

```powershell
$env:OPERAMIND_TEST_DATABASE_URL = "postgresql://.../operamind_test"
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m mypy
.\.venv\Scripts\python.exe -m pytest -q `
  --ignore=tests/integration/test_live_embedding_provider.py `
  --ignore=tests/integration/test_golden_screen_change.py `
  --cov=operamind --cov-report=term-missing `
  --cov-report=json:coverage.json --cov-fail-under=80
.\.venv\Scripts\python.exe scripts\check_critical_coverage.py
```

`tests/conftest.py` がセッション DB の lifecycle を所有し、テスト専用の `tests/support/postgres.py` が安全な DB 名、`template0`、migration、`DROP DATABASE ... WITH (FORCE)` を実装します。既存 DB を直接テスト対象として再利用するモードはありません。

`scripts/check_critical_coverage.py` は平均値だけを見ません。ポリシーに列挙された各ファイルを個別に検査し、レポートからファイルが消えた場合も失敗します。既定の重要ファイル閾値は 80% ですが、`file_minimum_percent_overrides` に列挙した Approval、Copilot 権限／影響範囲、Main Change Flow、実行／Recovery などの高リスク中核ファイルには 90% を適用します。全ファイルを一律 90% にするのではなく、達成済みの中核モジュールを段階的に 90% 門禁へ固定します。閾値や対象ファイルを変更すると source-tree digest も変わるため、古い full-regression Evidence は自動的に stale になります。

テスト件数、skip 件数、Coverage 値はこの文書へ手入力しません。GitHub Actions は Pytest、Windows Python、VS Code Extension の JUnit XML と `coverage.json` から Job Summary を毎回生成し、同じ内容を `quality-results-*` Artifact として保存します。現在値は対象 Commit の Job Summary と Artifact を参照します。

通常の Quality Job は隔離 PostgreSQL を使用しますが、外部サービスを必要とする実 Embedding と実 Playwright は日常回帰から明示的に除外します。実 Embedding／RAG、実 Copilot 閉ループ、実ブラウザ Screenshot Evidence、対象システムの Gradle build、Windows native 統合確認は `RECONSTRUCTION.md` の未完了項目と `full_local_regression` で管理し、通常回帰の passed 件数で完了扱いにしません。

2026-07-29 の追加精簡では、生成済み Test Case の自然言語修正を六工程 Web に接続し、VisionDemo 固有の target adapter を削除し、E2E Plan Builder と Code／Relation／Command Profile は契約テスト fixture へ隔離しました。対象固有 Binding は Web の `TestDataExecutorFactory` 拡張点から明示注入し、既定 CLI は未登録 Fixture／SQL／UI 操作を fail closed にします。新規利用できない旧 `UiLocatorProfile` と Browser Manifest の設定例も削除しました。`UiVerificationResult v2` は current Orchestration と TestDataExecutionResult の ID を必須とし、Closure、Readiness、Profile Drift／Rebuild、Test Case Revision は旧 UI Plan 台帳を fallback に使いません。migration `0060` は Closure の UI 外部キーを current Artifact store に切り替え、Readiness は同じ Orchestration の最新 TestData run だけを受理します。production source の旧 UI table query は 0 件で、再導入を阻止する contract test を追加しました。

同日の第二次精簡では、Copilot Impact と並存していた旧 deterministic Impact Report／Code Scope 管線、内部で直接ファイルを書き換える XLSX Proposal／Workspace Editor、production package 内のテスト DB helper、および TestDataPlan の前段にあった `BusinessDataTemplate` を削除しました。跨画面データは Copilot が生成する一つの TestDataPlan に変数、setup、assertion、逆順 cleanup として直接記録します。過去 source tree に結び付いた自動生成 readiness Evidence は削除し、`target_deployment_e2e` と `full_local_regression` を再採取まで明示的に pending としました。

## readiness と Golden RAG

二つの状態は役割が異なります。

- Repository readiness の唯一の正は、`MvpReadinessValidator` が Evidence の schema、digest、review と現在の source-tree digest を検証した「有効状態」です。Manifest に `passed` と書かれていても Evidence が古ければ API／CLI は `pending` と `stale` を返します。
- Golden RAG は、固定 Query と期待 Context に対するオフライン回帰品質の正です。Canonical PostgreSQL に保存された同一 Snapshot／Embedding Profile／current Search Index の `GoldenRagQualityReport` を品質変更時とリリース前に評価しますが、個別 Change Request の Impact 実行条件にはしません。

個別 Change Request は、現在の Project に対する ready/current Canonical Index、固定 Embedding Profile、検索結果の Project/Snapshot provenance が揃わなければ fail closed で停止します。README の実行メモ、過去の Golden 数値、別 Snapshot の Report はこの実行時 Evidence の代替になりません。

## full_local_regression Evidence

GitHub Actions の Coverage 実行は日常的な品質ゲートです。一方、`full_local_regression` は PostgreSQL と実 Microsoft Edge を含み、ゼロ failure／ゼロ skip を要求するリリース Evidence です。

```bash
.venv/bin/operamind-readiness --root . \
  run-full-regression \
  --project-id visiondemo \
  --analysis-case-id visiondemo-expense-status-filter-p6
```

このコマンドは最終 source tree の digest を記録します。Evidence 生成後に source、テスト、migration、Contract／Profile、依存ロック、品質ポリシー、品質 Workflow または検証スクリプトを変更すると、その Evidence は無効になります。したがって、最終コミットを確定し、必要な PostgreSQL と Microsoft Edge を利用できる検証環境で再生成します。
