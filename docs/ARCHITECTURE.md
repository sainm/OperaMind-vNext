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

Copilot が同一 content digest に対する必須コンパイル／テスト／Coverage と committed EditResult の成功後に作った実ブラウザ用 UiTestPlan / TestDataPlan を受け取る。OperaMind は全 Business Rule を、実行可能 UI Test、検証済み Code Test、または現在 Task に実在する Command／Canonical Artifact／Plan Component Evidence に照合してカバレッジを再計算し、100% 未満なら未カバー Rule を同じ Copilot `test_planning` Task に返して完全版を再生成させる。Evidence は型に対応する許可済み参照へ解決できなければ採用しない。100% 未満の Plan は永続化、人工確認、Test Data 実行、UI Test に進めない。現在の標準実行器は Project 固有 `test_base_url` に限定した HTTP / Playwright UI であり、未登録の Fixture / SQL Binding を含む Plan は確認前に阻断する。Cleanup は失敗時も試行する。UI Step ごとのサニタイズ済み Screenshot と Step Log を保存し、実行結果が揃った場合だけ `UiVerificationResult` と最終 Closure Report を自動生成する。Web の自然言語修正は read-only Copilot revision Task を経由し、同じ Automation Run の上流確認を維持したまま TestPlan 以降だけ再確認・再実行し、旧 Run／Evidence／Closure／Report を stale として無効化する。

### Closure

要件、StructuredChange、ImpactReport、EditResult、Command Evidence、TestData、Business Coverage、UI Result を結合し、欠落があれば fail closed にする。
