# VS Code GitHub Copilot ワークフロー

## 方針

VS Code 上の GitHub Copilot を、設計書変更、コード変更、TestPlan 生成の唯一の編集 AI とする。OperaMind Web と CLI に別の AI 入力経路を作らない。

## Change Task

一つの Change Task は次の成果物を順番に扱う。

1. `document_change`: RAG の Section 候補を `document_id` で完全な Canonical 文書と実ファイル参照へ戻し、選んだ設計書を変更して実差分を Canonical 化する
2. `code_scope`: 変更済み設計書を根拠に、Code Graph で検証可能な Path / Symbol / Test Binding を提示する
3. `compile_test`: OperaMind が検証したコード範囲内でコードを変更し、作業差分を検証する
4. `test_planning`: 検証済みコード差分、設計書差分、要件から TestPlan / TestDataPlan を生成する
5. `ui_validation`: OperaMind がデータ生成後に TestDataPlan の UI Step / Assertion を実行する

各成果物は Copilot が直接 Canonical DB に書かない。MCP Tool が Schema、パス、Revision、差分、参照関係を検証し、成功した候補だけを保存する。

## VS Code 側

- コード用 linked worktree と設定済み設計書 Root を同じ VS Code Workspace で開く。
- Bridge Token は SecretStorage に保存する。
- 拡張は Project、Workspace、Git common-dir を確認してから Change Task を受け取る。
- Copilot は MCP から受け取った対象範囲だけを読み書きする。
- 範囲外ファイル、新しい破壊的コマンド、異なる Revision が必要になった場合は停止理由を返す。

## OperaMind 側

- Requirement から RAG で Section 候補を検索し、各候補の `document_id` から完全な Canonical 文書、論理名、実文書参照を復元する。
- 文書変更後に StructuredChange を生成する。
- StructuredChange と Copilot の読み取り専用 Scope 提案を Code Graph で検証し、ImpactReport を生成する。
- コード変更後に Git Diff を検証し、限定 Grant に含まれる設定済みコマンドが一件も欠けず成功したことを検証する。
- コード差分検証後に限り、Copilot の自然言語 TestPlan と実行可能 TestDataPlan を Canonical TestPlan / TestDataPlan / AcceptanceCriteria に変換・検証する。
- TestDataPlan のデータ生成、有限 UI Action / Assertion、Screenshot Evidence、UiVerificationResult を順に自動実行する。
- Copilot が生成した Test Case は六工程 Web から自然言語で修正できる。OperaMind は構造化差分と曖昧な候補を先に表示し、利用者の一括確認後だけ新しい TestPlan／TestDataPlan Version と下流実行を作る。この確認処理は編集 AI の別経路ではない。
- HTTP／UI Step の target Origin は `OPERAMIND_TEST_TARGET_BASE_URL` だけから取得し、未設定または資格情報を含む URL は fail closed にする。
- Web には Change Task の内部 Phase や Queue を出さず、六工程の状態だけを出す。

## 現在の移行状態

`CopilotCodingTask v2` は六工程を一つの Change Task として配信する。Copilot は同じ会話で `copilot_record_change_outputs` を三回使い、`document_change`、`code_scope`、`test_planning` を順に記録する。OperaMind は各回の Schema、Canonical provenance、Code Graph、現在 Stage を検証し、次へ進める場合だけ `next_context` を返す。TestPlan は `copilot_validate_task_diff` が現在のコード差分を受理する前には保存できない。旧ファイル handoff、別 Draft CLI、ローカル Checkpoint は使用しない。

Copilot に公開する MCP Tool は `copilot_get_coding_task`、`copilot_record_change_outputs`、`copilot_validate_task_diff`、`copilot_run_task_command`、`copilot_record_task_result` の五つだけである。Case 一覧、Impact／Edit Packet／Approval Grant の個別取得、旧 worktree／Edit Result、UI Plan／Validation Result の直接 Tool は公開しない。必要な内部情報と安全確認は五つの Change Task Tool が現在 Stage に応じて組み立てる。

Copilot の成果物が Canonical DB に記録された後は、OperaMind Web プロセス内の Coordinator が内部状態を監視し、確定的な遷移、TestDataPlan の予約・実行、UI 検証、Closure 更新を自動で進める。画面は `GET /flow` で六工程を読むだけであり、画面の更新操作をワークフロー進行条件にしない。
