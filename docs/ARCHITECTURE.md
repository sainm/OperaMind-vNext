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
  ├─ Test Runtime ─── Test Data → bounded UI steps / Playwright
  └─ Closure ──────── Coverage / Evidence / Final Report
```

## 信頼境界

- PostgreSQL の正規化データと不変 Artifact が Canonical Data である。
- Vector Index は検索用であり、正本ではない。
- Git Revision、Document Snapshot、Profile Version、Deployment を Evidence に固定する。
- AI 出力は候補であり、Schema、範囲、Revision、差分、コマンド結果を検証してから Canonical 化する。
- Web は内部の承認・Queue・Lease を表示せず、六工程の状態に投影する。

## コンポーネント

### Web

変更要件一覧、六工程、各工程の成果物、停止理由、最終レポートだけを表示する。管理コンソールや Worker 操作画面は持たない。

### Local Bridge / MCP

VS Code GitHub Copilot に対して、対象変更に結び付いた限定タスクを配信する。Bridge Token は VS Code SecretStorage に保持し、平文を DB や画面に保存しない。

### Document RAG

Requirement から関連 Document ID を検索し、Canonical DB で本文と関係を復元する。Index 未準備、Profile drift、Project 漏えいがある場合は `document_change` または `code_scope` を停止する。

### Code Graph / Impact

固定 Git Revision を解析し、変更候補ファイル、Symbol、Route、Test Binding を生成する。未解決 Edge は停止理由として残し、AI の推測で閉じない。

### Test Runtime

Copilot が検証済みコード差分から作った TestPlan / TestDataPlan を受け取り、Fixture / API / SQL で画面横断データを生成した後、同じ Plan の有限 UI Action / Assertion を実行する。Cleanup は失敗時も試行する。UI Step が成功しサニタイズ済み Screenshot が揃った場合だけ `UiVerificationResult` を自動生成する。

### Closure

要件、StructuredChange、ImpactReport、EditResult、Command Evidence、TestData、Business Coverage、UI Result を結合し、欠落があれば fail closed にする。
