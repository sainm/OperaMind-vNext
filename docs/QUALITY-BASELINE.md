# 再現可能な品質ベースライン

このリポジトリの品質判定は、ローカルの印象や README の記述ではなく、固定されたコマンド、Coverage JSON、`quality/critical-coverage.json`、および検証済み readiness Evidence を正とします。

## 自動品質ゲート

GitHub Actions の `.github/workflows/quality.yml` は、Python 3.12、ロック済み依存関係、PostgreSQL 18 + pgvector 0.8.2 を使用し、次を実行します。

1. Ruff
2. Mypy strict
3. PostgreSQL 統合テストを含む Pytest
4. リポジトリ全体の statement coverage 80%
5. readiness／Golden RAG、Approval、Copilot、Task scheduling、Recovery の各重要ファイル coverage 80%

ローカルでは空の専用テスト DB を用意し、次のように同じゲートを再現します。

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

`scripts/check_critical_coverage.py` は平均値だけを見ません。ポリシーに列挙された各ファイルを個別に検査し、レポートからファイルが消えた場合も失敗します。閾値や対象ファイルを変更すると source-tree digest も変わるため、古い full-regression Evidence は自動的に stale になります。

## readiness と Golden RAG

二つの状態は役割が異なります。

- Repository readiness の唯一の正は、`MvpReadinessValidator` が Evidence の schema、digest、review と現在の source-tree digest を検証した「有効状態」です。Manifest に `passed` と書かれていても Evidence が古ければ API／CLI は `pending` と `stale` を返します。
- Golden RAG の唯一の正は、Canonical PostgreSQL に保存された同一 Snapshot／Embedding Profile／current Search Index の最新 `GoldenRagQualityReport` です。最新 Report が `passed` でなければ Impact 生成と確認は fail closed で停止します。

README の実行メモや過去の数値はどちらの代替にもなりません。Repository readiness と Golden RAG の両方が有効な場合だけ、実 RAG を含む変更処理を ready と判断します。

## full_local_regression Evidence

GitHub Actions の Coverage 実行は日常的な品質ゲートです。一方、`full_local_regression` は PostgreSQL と実 Microsoft Edge を含み、ゼロ failure／ゼロ skip を要求するリリース Evidence です。

```bash
bash scripts/regenerate-readiness-wsl.sh \
  visiondemo \
  visiondemo-expense-status-filter-p6
```

このコマンドは最終 source tree の digest を記録します。Evidence 生成後に source、テスト、migration、Contract／Profile、依存ロック、品質ポリシー、品質 Workflow または検証スクリプトを変更すると、その Evidence は無効になります。したがって、最終コミットを確定してから WSL + Podman + Microsoft Edge で再生成します。
