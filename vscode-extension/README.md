# OperaMind Copilot Bridge Extension

この拡張は OperaMind Web の loopback Bridge から、現在の隔離 linked worktree に対応する `CopilotCodingTask` を取得します。ユーザーが日文ダイアログで確認するまで MCP から Task Context を取得できません。確認後は VS Code 上の GitHub Copilot Chat を `copilot_coding_plan` で開き、テスト、path-only Diff、commit 結果を `operaMind` MCP 経由で Canonical ledger に戻します。

ソース、設計書、prompt、Diff 本文、テストログ本文を Bridge の中継ファイルには書きません。Bridge Token は VS Code SecretStorage に保存し、接続先は loopback URL のみ許可します。Workspace Trust が無効な Workspace では拡張を実行しません。

## VSIX の作成とインストール

```bash
cd vscode-extension
npm ci
npm run package:vsix
```

生成物は `dist/operamind-copilot-bridge.vsix` です。VS Code の Command Palette で `Extensions: Install from VSIX...` を実行し、このファイルを選びます。インストール後、対象 linked worktree を開いて VS Code を再読み込みし、`OperaMind: Bridge Token を安全に登録` で Web と同じ Token を登録します。

開発時はこのディレクトリを VS Code で開き、`F5` の `OperaMind Bridge Extension POC` を選んで Extension Development Host を起動できます。

## ローカル環境診断

Command Palette の `OperaMind: ローカル環境を診断` は、インストール済み VSIX Version、loopback Bridge、Workspace Trust、隔離 linked worktree、13 個の MCP tools、GitHub Copilot Chat と利用可能な Copilot model を読み取り専用で確認します。Copilot へ prompt は送信しないため、この診断自体は Chat Credit を消費しません。

Bridge Token が登録済みなら、Version、真偽値、MCP tool 名、Workspace path の SHA-256 fingerprint だけを Web に送信します。Token、DB URL、Workspace path、ソースコードは送信しません。結果は Web process のメモリに最大 15 分だけ保持し、5 分を過ぎた観測は現在状態の判定に使用しません。

診断は Workspace Trust、認証情報、Migration を自動変更しません。修復指引を確認し、ユーザー自身が必要な操作を選択してください。GitHub Copilot の Credit／Quota は model 一覧だけでは確定できないため、実会話前に VS Code の表示を確認します。

## 復旧、取消、再試行

- Task claim は 60 秒の lease です。拡張は polling 時に lease を更新します。
- VS Code または Bridge が切断されても、拡張は `workspaceState` に Task ID を保持し、再起動後に `OperaMind: 現在のタスクを再開` で同じ `accepted`／`in_progress` Task を再取得します。
- lease が失効した Task は、同じ Workspace の新しい consumer が取得でき、Canonical Event に `claim_recovered` を記録します。旧 consumer の accept は拒否されます。
- `OperaMind: 現在のタスクを取消` は理由付きで Task を `cancelled` にします。Web からも取消できます。
- 再試行は旧 Task を上書きせず、新しい Task ID、`retry_of_coding_task_id`、増加した `attempt_number` を持つ不可変 Task を Web から作成します。現在有効な Packet、Grant、Workspace を再度指定するため、失効 Grant を暗黙に再利用しません。
- Bridge Client は一時的な network／5xx エラーを最大 3 回再試行します。認証、契約、範囲エラーは自動再試行しません。

```bash
npm test
```

本番 API Provider は `coding_task_provider_v1` を実装し、同じ `CopilotCodingTask` を受け取ります。現在の Web use case は `local_bridge` と `vscode_github_copilot` に固定しています。
