# MVP スコープ

## 対象

- 自然言語の変更要件
- 設定済みローカル設計書
- ローカル Git Repository と隔離 linked worktree
- VS Code GitHub Copilot + Local Bridge + MCP
- ローカル Embedding Provider
- PostgreSQL 18 + pgvector
- Java / Spring Boot / Struts 1、および既存の多言語 Code Graph Adapter
- テストデータ生成、コンパイル、コマンドテスト、Playwright UI 検証
- 六工程の日本語 Web と最終レポート

## 非対象

- OperaMind 独自の LLM コード編集
- OpenAI / Claude などのリモート Coding API Provider
- 複数利用者向け認証・権限管理
- Web 上の Task Queue、Worker、Profile、Approval 管理
- Silver / Fake Evidence の自動 Golden 昇格
- 任意 Shell、任意 SQL、任意ブラウザ操作

## 完了条件

1. 変更要件から関連設計書を実 RAG で一意に特定できる。
2. VS Code GitHub Copilot が設計書変更、コード変更、TestPlan 生成を同じ Change Task として完了できる。
3. OperaMind が設計書差分、コード範囲、Git Diff、コンパイル、テスト結果を検証できる。
4. 一連の画面データを生成し、UI Scenario を実ブラウザで実行できる。
5. Web が六工程以外の内部操作を要求しない。
6. 最終レポートが現在 Revision と Evidence に結び付き、欠落時は成功にしない。
7. Ruff、mypy、単体、PostgreSQL 統合、Edge / Playwright E2E が通過する。
