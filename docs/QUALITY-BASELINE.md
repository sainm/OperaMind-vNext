# 再現可能な品質ベースライン

このリポジトリの品質判定は、ローカルの印象や README の記述ではなく、固定されたコマンド、Coverage JSON、`quality/critical-coverage.json`、および検証済み readiness Evidence を正とします。

## 自動品質ゲート

GitHub Actions の `.github/workflows/quality.yml` は、Python 3.12、ロック済み依存関係、PostgreSQL 18 + pgvector 0.8.2 を使用し、次を実行します。

1. Ruff
2. Mypy strict
3. PostgreSQL 統合テストを含む Pytest
4. リポジトリ全体の statement coverage 80%
5. readiness／Golden RAG、Approval、Copilot／Project Stack、Main Change Flow、Task scheduling、Recovery の各重要ファイル coverage 80%

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

`tests/conftest.py` がセッション DB の lifecycle を所有し、テスト専用の `tests/support/postgres.py` が安全な DB 名、`template0`、migration、`DROP DATABASE ... WITH (FORCE)` を実装します。既存 DB を直接テスト対象として再利用するモードはありません。

`scripts/check_critical_coverage.py` は平均値だけを見ません。ポリシーに列挙された各ファイルを個別に検査し、レポートからファイルが消えた場合も失敗します。閾値や対象ファイルを変更すると source-tree digest も変わるため、古い full-regression Evidence は自動的に stale になります。

2026-07-28 の内部回帰では 612 passed、1 skipped、総 coverage 83.59% で、Copilot Change Task 84.17%、内部 Main Flow Coordinator 83.78%、TestData／UI／Closure 実行 88.71% を含む重要ファイルの個別 80% gate も通過しました。Mypy strict は 165 source files、VS Code Extension は 8 tests を通過しています。テスト件数の減少は、旧手動 CLI、documents／requirement dual-entry Planner／Batch、停止済み monolithic Executor、公開先を失った旧 Control Plane／UI Review Query 層、主閉ループと接続されていなかった旧 UiKnowledge／BrowserManifest／UiVerificationPlan 管線、および Copilot 計画を迂回して Frozen Golden Case から TestPlan を生成する旧 runtime fallback の専用テストを実装と一緒に削除したためです。旧 UI Plan 用 Grant 認可、旧 Validation 進捗 Query、二重 Screenshot origin、旧 UI Environment／Deployment 経由の target URL 解決も現行 TestDataPlan／Closure 契約へ統合しました。Golden RAG のオフライン品質基準、UI Evidence の保存、TestDataPlan の跨画面変数／UI 参照／cleanup 検証、および TestDataPlan 起点の UI 実行経路は維持しています。1 件は `OPERAMIND_PLAYWRIGHT_LIVE` を必要とする実 Edge テストです。この結果は Python／PostgreSQL 側の回帰確認であり、切替後の対象環境で行う Gradle build と Microsoft Edge の `full_local_regression` Evidence を代替しません。

2026-07-29 の追加精簡では、生成済み Test Case の自然言語修正を六工程 Web に接続し、VisionDemo 固有の target adapter を削除し、E2E Plan Builder と Code／Relation／Command Profile は契約テスト fixture へ隔離しました。対象固有 Binding は Web の `TestDataExecutorFactory` 拡張点から明示注入し、既定 CLI は未登録 Fixture／SQL／UI 操作を fail closed にします。新規利用できない旧 `UiLocatorProfile` と Browser Manifest の設定例も削除しました。`UiVerificationResult v2` は current Orchestration と TestDataExecutionResult の ID を必須とし、Closure、Readiness、Profile Drift／Rebuild、Test Case Revision は旧 UI Plan 台帳を fallback に使いません。migration `0060` は Closure の UI 外部キーを current Artifact store に切り替え、Readiness は同じ Orchestration の最新 TestData run だけを受理します。production source の旧 UI table query は 0 件で、再導入を阻止する contract test を追加しました。

同日の第二次精簡では、Copilot Impact と並存していた旧 deterministic Impact Report／Code Scope 管線、内部で直接ファイルを書き換える XLSX Proposal／Workspace Editor、production package 内のテスト DB helper、および TestDataPlan の前段にあった `BusinessDataTemplate` を削除しました。跨画面データは Copilot が生成する一つの TestDataPlan に変数、setup、assertion、逆順 cleanup として直接記録します。過去 source tree に結び付いた自動生成 readiness Evidence は削除し、`target_deployment_e2e` と `full_local_regression` を再採取まで明示的に pending としました。

現在の Mypy strict は 156 source files、VS Code Extension は 8 tests、Golden Dataset の構造検証は合格しています。PostgreSQL が停止している現端末で実行可能な回帰は 534 passed、54 skipped で、skip は PostgreSQL 依存 50 件、local-only Golden source 2 件、live Embedding 1 件、live Browser 1 件です。PostgreSQL を含む coverage gate はこの状態では再判定せず、上記 83.59% の記録を新しい source tree の Evidence として再利用しません。

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
