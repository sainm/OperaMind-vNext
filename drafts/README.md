# Change Draft

`change-draft-ai-response.schema.json` は、AI が生成する未信頼の変更案を制約します。AI コマンドは shell を使わず、読み取り専用 worktree で実行されます。設計書セル、コード候補、完全一致置換、テスト、テストデータ、受入基準、UI シナリオ、および確認用の選択肢を提案できます。

`change-draft-session.schema.json` は、`document_change`、`code_scope`、`edit_plan`、`verification_plan` の四段階の確認状態と回答者を記録します。ローカル側はコードパス、置換前像、文書の根拠、Case 内参照を再検証します。AI 出力だけで承認状態になることはありません。

`codex-implementation-rehearsal.schema.json` は、Copilot Free の停止中に Codex が作る実装予行案を制約します。予行案は固定 Session、Edit Packet、Base Revision に結び付けられ、常に `executable=false`、`automatic_apply_allowed=false` です。対象 worktree には書き込まず、VS Code 上の GitHub Copilot が現行 Packet と Grant を再検証して自分で編集する場合だけ参照できます。承認済み制約と固定ソースが矛盾する場合は `needs_reanalysis` とし、候補変更を含めません。
