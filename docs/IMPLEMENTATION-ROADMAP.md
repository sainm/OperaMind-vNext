# 実装ロードマップ

## R0: 製品面の収束

- 六工程 `MainChangeFlow` API
- 六工程だけを表示する日本語 Web
- 管理／承認／Worker／Profile UI の削除
- 重複文書と公開 CLI の整理

## R1: Copilot Change Task

- Draft file handoff と Coding Task の統合
- 設計書 Root と linked worktree を持つ Workspace Binding
- `document_change`、`code_scope`、`test_planning` の Phase 契約
- VS Code Extension と MCP Tool の更新
- 再開、取消、範囲逸脱の fail-closed 処理

## R2: 自動内部制御

- 文書差分の機械検証
- Impact 項目の安全な自動確定
- 実行範囲の内部 Grant
- Queue / Worker / Retry の Web 非公開化
- 業務上の停止理由への投影

## R3: TestPlan と UI Closure

- Copilot TestPlan の Schema・参照・業務 Coverage 検証
- 画面横断 TestDataPlan
- Playwright UI 実行
- ChangeClosureResult / 最終レポート

## R4: 削除と品質凍結

- 到達不能 CLI / Application / Repository / migration の監査
- 旧互換コードとテストの削除
- PostgreSQL 統合、Coverage、Edge / Playwright E2E
- 現在 Commit に結び付く Evidence 再生成
