# OperaMind Copilot Bridge

OperaMind の変更タスクを、loopback Bridge から VS Code 上の GitHub Copilot Chat へ渡すローカル拡張です。

設計書、ソース、Diff 本文、テストログ本文を Bridge の中継ファイルへ書きません。Bridge Token と短期 Claim Token は VS Code SecretStorage に保存し、接続先は loopback URL だけを許可します。Claim Token は設計書学習 Task の所有権確認にだけ使用し、Task の終端で削除します。未信頼 Workspace では診断以外を実行しません。

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

OperaMind Web の「VS Code で開く」からも、選択中プロジェクトのコード Workspace を直接開けます。すでに同じ Workspace を開いている場合は、画面を切り替えずに最新の人工確認または Coding Task を取得します。URI には Workspace と変更番号だけを含め、Bridge Token は含めません。macOS／Linux の絶対 Path と Windows の Drive／UNC Path に対応します。

Extension は Workspace 内の `.vscode/mcp.json` や Python を参照しません。OperaMind Launcher がユーザー領域へ保存した `runtime.json` を読み、同じ配布版を MCP stdio Server として起動します。Bridge Token もユーザー領域の専用 File から SecretStorage へ自動同期します。「Bridge Token を設定」は復旧用としてだけ残します。

## Activity Bar コントロール

日文コントロール画面は VS Code の Theme に追従する Webview です。Bridge 接続、Workspace、Coding Task、実行状態をカードで表示します。変更要件、工程確認、差戻し理由、TestPlan の自然言語修正は OperaMind Web だけで入力します。VS Code 側は確定済み Task の内容を読み取り専用で受け取り、「確認して Copilot を開く」「現在のタスクを再開」「最新状態に更新」「ローカル環境を診断」「OperaMind Web を開く」だけを提供します。Token の手動設定は自動同期に失敗した場合のフロー外復旧用です。

画面上部の更新 Icon でも最新状態を取得でき、Web Icon は設定済みの loopback OperaMind Web を既定 Browser で開きます。従来の Command Palette 入口も互換のため残します。専用 Shortcut は不要です。

拡張は Task ID を Workspace State に保持します。VS Code 再起動後も `OperaMind: 現在のタスクを再開` から同じ変更へ戻れます。Task の差戻しと取消は OperaMind Web で監査理由を記録します。

## ローカル環境診断

`OperaMind: ローカル環境を診断` は VSIX Version、Bridge、Workspace Trust、linked worktree、MCP Tool、GitHub Copilot Chat を読み取り専用で確認します。Token、DB URL、Workspace path、ソースコードは送信しません。

## Change Task v2

拡張は `copilot_change_task` または `ui_test_plan_revision` を受け取り、現在工程だけの最小 Prompt を Copilot Chat に渡します。Prompt は Task ID、Workspace、業務要件、現在工程の目的・入力・出力・停止条件だけを表示します。将来工程の手順、Schema、Command、認可情報は対話文に重複させず、MCP の `inputs`、`constraints`、`stage_contract` に分離します。

`copilot_record_change_outputs` は現在工程の成果物だけを記録します。

1. `output_stage=document_change`: RAG 対象文書の実差分を記録
2. `output_stage=code_scope`: Code Graph で検証する Path / Symbol / Test Binding を記録
3. `copilot_record_task_result`: 同一 Diff の必須コマンド、Coverage report、commit を記録
4. `output_stage=test_planning`: committed EditResult と変更行 Coverage 成功後の UiTestPlan / TestDataPlan を記録
5. `output_stage=ui_test_revision`: Web で確認された自然言語修正から完全な UiTestPlan / TestDataPlan を再生成

全五 Tool は、業務結果を `result`、工程状態を共通の `stage_status` で返します。`stage_status.next_action` は `perform_current_stage`、`continue_current_stage`、`reload_current_task`、`wait_for_confirmation`、`resolve_blocker`、`stop` のいずれかです。次工程の完全な Context は結果に複製せず、`reload_current_task` の場合だけ同じ Task ID を再取得します。Tool の可視テキストは日本語の一行要約とし、正規データは `structuredContent` に一度だけ格納します。UI Case は TestDataPlan の有限 UI Step / Assertion として表現し、OperaMind がデータ生成後に実行して Screenshot Evidence を保存します。

Copilot が使用する公開 Tool は `copilot_get_coding_task`、`copilot_record_change_outputs`、`copilot_run_task_command`、`copilot_validate_task_diff`、`copilot_record_task_result` の五つです。旧 Case／Impact／Approval／Packet／UI 個別 Tool は公開しません。
