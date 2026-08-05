# VS Code GitHub Copilot ワークフロー

## 方針

VS Code 上の GitHub Copilot を、設計書変更、コード変更、TestPlan 生成の唯一の編集 AI とする。OperaMind Web と CLI に別の AI 入力経路を作らない。

## Change Task

一つの Change Task は次の成果物を順番に扱う。

1. `document_change`: RAG の Section 候補を `document_id` で完全な Canonical 文書と実ファイル参照へ戻し、選んだ設計書を変更して実差分を Canonical 化する
2. `code_scope`: 変更済み設計書を根拠に、Code Graph で検証可能な Path / Symbol / Test Binding を提示する
3. `compile_test`: OperaMind が検証したコード範囲内でコードを変更し、作業差分を検証する
4. `test_planning`: 必須コンパイル／テスト／カバレッジ成功後、検証済みコード差分、設計書差分、要件から実ブラウザ用 UiTestPlan / TestDataPlan を生成する
5. `ui_test_revision`: Web で確認された自然言語修正を受け取り、コード・設計書を変更せず、完全な UiTestPlan / TestDataPlan を再生成する
6. `ui_validation`: OperaMind がデータ生成後に TestDataPlan の UI Step / Assertion を実行する

各成果物は Copilot が直接 Canonical DB に書かない。MCP Tool が Schema、パス、Revision、差分、参照関係を検証し、成功した候補だけを保存する。

## VS Code 側

- 変更要件、工程判断、差戻し／取消理由、TestPlan の自然言語修正を入力しない。これらは OperaMind Web の責務とし、VS Code は確定済み Task の取得、開始／再開、Copilot 実行状況の表示だけを行う。
- コード用 linked worktree と設定済み設計書 Root を同じ VS Code Workspace で開く。
- Launcher が生成した Bridge Token を拡張が SecretStorage に自動同期する。
- 設計書学習 Task の短期 Claim Token も SecretStorage に保持し、MCP の取得・記録時に所有権と有効期限を検証して、Task の終端で削除する。
- MCP は拡張の Server Definition Provider から登録し、対象 Workspace に `.vscode/mcp.json` を作らない。
- 拡張は Project、Workspace、Git common-dir を確認してから Change Task を受け取る。
- Copilot は MCP から受け取った対象範囲だけを読み書きする。
- 範囲外ファイル、新しい破壊的コマンド、異なる Revision が必要になった場合は停止理由を返す。
- Copilot Chat へは現在工程の業務 Prompt だけを送る。将来工程の手順や全 MCP Tool 一覧を自然言語 Prompt に重複しない。
- 全五 Tool は `{result, stage_status}` の共通 Envelope で返す。`copilot_get_coding_task` の `result` は `inputs`、`constraints`、`stage_contract` を分離し、業務説明と Schema／Path／Command などの機械制約を混在させない。
- 次工程の完全 Context は結果に複製しない。`reload_current_task` の場合だけ Task を再取得する。

## OperaMind 側

- `ChangeFlowStateMachine` を内部 Automation Stage、Web 六工程、MCP `stage_status`、Copilot の実行可能工程、Coordinator の自動 Action に対する唯一の状態解釈とする。各 Surface は状態を独自判定せず、同じ投影を表示または実行する。未知 Stage／Status、早すぎる将来工程、矛盾する完了状態は fail closed にする。
- Requirement から RAG で Section 候補を検索し、各候補の `document_id` から完全な Canonical 文書、論理名、実文書参照を復元する。
- 文書変更後に StructuredChange を生成する。
- StructuredChange と Copilot の読み取り専用 Scope 提案を Code Graph で検証し、ImpactReport を生成する。
- コード変更後に Git Diff を検証し、限定 Grant に含まれる設定済みコマンドが一件も欠けず成功したことを検証する。
- コード差分検証後に限り、Copilot の自然言語 TestPlan と実行可能 TestDataPlan を Canonical TestPlan / TestDataPlan / AcceptanceCriteria に変換・検証する。OperaMind は全 Business Rule を母数として業務カバレッジを自動計算し、100% 未満なら未カバー Rule ID と本文を同じ `test_planning` Task のエラーとして Copilot に返す。Copilot は完全な両 Plan を再生成して再送し、100% になるまで Task を完了しない。
- TestPlan の各自然言語 Step と TestDataPlan の Playwright UI Step を `step_id` / `test_step_refs` で対応付け、未対応、範囲外参照、非実行 Step への参照を fail closed にする。
- Copilot は画面設計書、HTML／Template／Frontend Source、Route、Code Graph、Test Case、TestDataPlan、DataIdentityProvider を根拠に、画面ごとに role／label／placeholder／test id／title／alt text／安全な CSS／複合 Identity Locator を選ぶ。状態変更 Action は期待値付き `pre_action_observations` を持つ。Locator 失敗は正式 Run の脱敏 Evidence として同じ Change Task に返し、OperaMind が read-only `ui_test_plan_revision` Task を自動発行する。Copilot は業務期待値を変更せず新しい Plan Revision を作るため、利用者は失敗情報を手で転記しない。新 Revision は同じ Confirmation API で最終確認し、元 Run の途中では適用しない。
- 業務 Rule を `covered` にできるのは、TestDataPlan の同一 Case Flow、Test Data、Playwright Action、Observation、Assertion まで解決できる UI Test、または現在 Task の実在 Source に解決できる型付き Evidence だけである。Code Test は承認済み Test File と現在 Revision で成功した Command の両方、Command／Canonical／Plan Evidence はそれぞれ成功済み Command、Canonical Artifact、実在 Plan Component を必須とする。単なる Rule ID、説明文、未知の参照はカバレッジ値を上げない。
- 人工確認は業務カバレッジが 100% になった最終 UiTestPlan／TestDataPlan だけを対象とする。100% 未満の Draft は保存・編成・人工確認・Test Data 実行・実ブラウザ UI Test のいずれにも進めない。利用者は不足要件の発見や補完を担当しない。
- TestDataPlan のデータ生成、有限 UI Action / Assertion、Screenshot Evidence、UiVerificationResult を順に自動実行する。
- TestDataPlan の各 `test_data_id` は `inputs.planning.data_identity_providers` に列挙された実 Provider（database／api／ui／hybrid）だけを選ぶ。Provider は実観測から `primary_key`、`business_unique_keys`、`screen_identity_values`、`record_scope_locator`、`match_count`、`evidence_ref` を同一形式で返し、`match_count=1` の場合だけ Run 固有 Binding／digest／Evidence として固定する。未登録 Provider、source Evidence 欠落、fake、推測、静かな fallback は blocked とする。Secret、接続情報、認証値は Plan、Copilot Context、DB、ログ、Evidence に含めない。Copilot は業務一意キーと画面識別値ごとに、同じ bound record の実 DOM から値を読む exact `dom_observation` を定義する。全 UI Step は `operation_scope=screen|bound_record` を明示し、跨画面・表 UI Step は `bound_record` と `data_binding_ref` と exact Locator でこの 1 件だけを操作する。OperaMind は操作前に Scope count=1 と DOM 実値を確認し、自身で observed identity digest を計算する。期待 digest の流用、分類欠落・矛盾、0 件、複数件、DOM 身元不一致、drift、行番号、曖昧 Text、AI 推測は停止理由となり、固定済み Scope に computer-use fallback は使わない。
- Copilot は全 AcceptanceCriteria／TestCase／TestData の組合せに `coverage_conditions` を出力するが、Coverage 値や成功判定は出力しない。OperaMind が確認済み SQL readback の実測値から項目、状態、境界値、関連関係を検証し、期待値／実測値／判定を Evidence に固定する。Test Data Coverage が 100% になる前に TestPlan の UI Step を開始しない。
- Copilot が生成した UiTestPlan／TestDataPlan は六工程 Web から自然言語で修正できる。対象はテスト Step、期待結果、データ生成 Step、跨画面変数の取得元、業務 Assertion、クリーンアップ Step である。OperaMind は構造化差分と曖昧な候補を先に表示し、利用者の一括確認後だけ read-only revision Task を Copilot に渡す。Copilot の完全な再生成結果が検証されるまで旧 Version は current のままで、検証後に新しい UiTestPlan／TestDataPlan Version と下流実行を作る。同時に旧 Run／UI Evidence／Closure／Report は stale として無効化する。
- HTTP／UI Step の target Origin は Project の `test_base_url` だけから取得する。Application Context Path は保持するが、Query、Fragment、資格情報を含む URL または未設定値は TestPlan 確認前に fail closed にする。
- Web には Change Task の内部 Phase や Queue を出さず、六工程の状態だけを出す。

## 現在の移行状態

`CopilotCodingTask v2` は六工程を一つの Change Task として配信する。Copilot は `document_change`、`code_scope` を記録し、範囲内 Diff に対する compile／test／coverage、commit、`copilot_record_task_result` を完了した後で `test_planning` を記録する。OperaMind は Coverage report を直接解析し、各 Command と committed content digest の一致、Schema、Canonical provenance、Code Graph、現在 Stage を検証する。旧ファイル handoff、別 Draft CLI、ローカル Checkpoint は使用しない。

Copilot に公開する MCP Tool は `copilot_get_coding_task`、`copilot_record_change_outputs`、`copilot_validate_task_diff`、`copilot_run_task_command`、`copilot_record_task_result` の五つだけである。Case 一覧、Impact／Edit Packet／Approval Grant の個別取得、旧 worktree／Edit Result、UI Plan／Validation Result の直接 Tool は公開しない。必要な内部情報と安全確認は五つの Change Task Tool が現在 Stage に応じて組み立てる。

TestPlan / TestDataPlan の形式は `copilot_record_change_outputs` の MCP inputSchema を正とする。汎用の完全例を毎回 Context に追加せず、現在の Business Rule、必須 UI Scenario、許可済み Evidence だけを `inputs.planning` に渡す。Schema 違反はフィールド位置付きで fail closed に返す。

Copilot の成果物が Canonical DB に記録された後は、OperaMind Web プロセス内の Coordinator が内部状態を監視し、確定的な遷移、TestDataPlan の予約・実行、UI 検証、Closure 更新を自動で進める。画面は `GET /flow` で六工程を読むだけであり、画面の更新操作をワークフロー進行条件にしない。

Coordinator は全 Project／Change Request を定期走査しない。自動実行可能な `next_action`、実行中の復旧対象、期限切れ Lease を持つ Run だけを PostgreSQL から最大 200 件取得し、期限切れの内部編成 Task は `effective_state=ready` として再取得する。
