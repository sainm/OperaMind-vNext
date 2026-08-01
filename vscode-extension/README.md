# OperaMind Copilot Bridge

OperaMind の変更タスクを、loopback Bridge から VS Code 上の GitHub Copilot Chat へ渡すローカル拡張です。

設計書、ソース、Diff 本文、テストログ本文を Bridge の中継ファイルへ書きません。Bridge Token は VS Code SecretStorage に保存し、接続先は loopback URL だけを許可します。未信頼 Workspace では診断以外を実行しません。

## インストール

```bash
cd vscode-extension
npm ci
npm run package:vsix
```

`dist/operamind-copilot-bridge.vsix` を VS Code の `Extensions: Install from VSIX...` でインストールします。

1. 対象 linked worktree を VS Code で開く。
2. Activity Bar の OperaMind アイコンから「コントロール」を開く。
3. OperaMind Launcher を起動する。Extension が MCP と Bridge Token を自動検出する。
4. 「確認して Copilot を開く」から変更タスクを確認する。
5. 確認 Dialog から GitHub Copilot Chat を開く。

Extension は Workspace 内の `.vscode/mcp.json` や Python を参照しません。OperaMind Launcher がユーザー領域へ保存した `runtime.json` を読み、同じ配布版を MCP stdio Server として起動します。Bridge Token もユーザー領域の専用 File から SecretStorage へ自動同期します。「Bridge Token を設定」は復旧用としてだけ残します。

## Activity Bar コントロール

日文コントロール画面は、Bridge 接続状態、現在の Workspace、Coding Task ID、実行状態を自動更新します。「確認して Copilot を開く」「現在のタスクを再開」「現在のタスクを取消」「最新状態に更新」「ローカル環境を診断」「OperaMind Web を開く」を画面から実行できます。Token の手動設定は自動同期に失敗した場合の復旧用です。

画面上部の更新 Icon でも最新状態を取得でき、Web Icon は設定済みの loopback OperaMind Web を既定 Browser で開きます。従来の Command Palette 入口も互換のため残します。専用 Shortcut は不要です。

拡張は Task ID を Workspace State に保持します。VS Code 再起動後も `OperaMind: 現在のタスクを再開` から同じ変更へ戻れます。取消は理由付きで記録し、再試行は新しい Task ID を使用します。

## ローカル環境診断

`OperaMind: ローカル環境を診断` は VSIX Version、Bridge、Workspace Trust、linked worktree、MCP Tool、GitHub Copilot Chat を読み取り専用で確認します。Token、DB URL、Workspace path、ソースコードは送信しません。

## Change Task v2

拡張は `copilot_change_task` を受け取り、要求、設計書変更、コード範囲、コンパイル／テスト、UI 検証、最終レポートの順序と、設計差分、コード差分、TestPlan、TestDataPlan の必須成果物を Copilot Chat に渡します。

`copilot_record_change_outputs` は同じ会話で三段階に使用します。

1. `output_stage=document_change`: RAG 対象文書の実差分を記録
2. `output_stage=code_scope`: Code Graph で検証する Path / Symbol / Test Binding を記録
3. `output_stage=test_planning`: コード差分検証後の TestPlan / TestDataPlan を記録

各 Tool は次へ進める場合だけ `next_context` を返します。UI Case は TestDataPlan の有限 UI Step / Assertion として表現し、OperaMind がデータ生成後に実行して Screenshot Evidence を保存します。旧ファイル handoff は使用しません。詳細は [主変更閉ループ再構成](../docs/RECONSTRUCTION.md) を参照してください。

Copilot が使用する公開 Tool は `copilot_get_coding_task`、`copilot_record_change_outputs`、`copilot_run_task_command`、`copilot_validate_task_diff`、`copilot_record_task_result` の五つです。旧 Case／Impact／Approval／Packet／UI 個別 Tool は公開しません。
