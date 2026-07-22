# Agent-neutral タスク編成プロトコル

## 目的

Change Automation の業務工程と実行主体を分離します。各工程は「誰が実行するか」ではなく、必要 Capability、入力 Evidence、期待する出力、受入条件、依存関係を持つ不変 `OrchestrationTask` として公開されます。Agent、Subagent、人は同じ API／CLI で Task を取得できます。

公開 Definition は `protocol_version=orchestration_task_v1` を持ち、Version を含む全 Definition が SHA-256 digest に固定されます。将来の新 Version は既存 Task を書き換えず並行して扱います。

現在の既定値は、一つの Automation Run に対して同時に一つだけ実行する単一 Agent 方針です。Control Plane が自動実行する確定的な編成処理も `operamind-single-agent` として同じ Claim／Heartbeat／Canonical 対帳を通り、特権的なバイパスを持ちません。将来 Subagent を追加するときは、Capability と並列数のスケジューリング方針だけを変更し、Change Request、設計書、Impact、TestData、UI、Closure の業務フローは変更しません。

## 境界

- `ChangeAutomationDecision` は Canonical Data から次の業務 Action を決定します。
- `OrchestrationTask` は Action を実行可能な契約に変換します。特定の Model、IDE、Provider、Worker ID は保存しません。
- `OrchestrationTaskClaim` は実行時だけ Executor Kind、Executor ID、Capability と期限付き Lease を結び付けます。
- `OrchestrationTaskResult` は結果、Artifact Ref、受入 Evidence を追加式で保存します。
- `CopilotCodingTask` はコード変更工程の local Bridge 配送 Adapter として残り、全体の業務状態機械にはなりません。

既存 Change Automation の `prepare_document_with_copilot`、`revise_document_with_copilot`、`apply_code_change_with_copilot` は互換用の業務 Adapter Key として残しますが、公開 Task では `prepare_document`、`revise_document`、`apply_code_change` と中立な指示へ変換します。したがって Task Consumer は VS Code や Copilot を実装条件にしません。

## 状態と Lease

```text
ready -> claimed -> running -> submitted -> completed
                         |          |
                         |          +-> Canonical 状態との対帳
                         +-> failed | blocked
             |          |
             +----------+-> released -> ready
             +----------+-> lease_expired -> ready
```

Task Definition は上書きしません。指示や受入条件が変わる場合は新しい Task ID を作成し、旧 Task を `superseded` にします。Claim Token の平文は DB に保存せず SHA-256 digest だけを保存します。Token は Claim 応答で一度だけ返され、Heartbeat、Release、Result 登録で必要です。期限切れ Token や別 Executor の Token では結果を登録できません。

Definition は Application 層だけでなく `0047` の DB Constraint でも検査されます。`eligible_executor_kinds` は `agent`、`subagent`、`human` の三者を必ず含む固定契約で、Capability、Output、Acceptance Criteria は空にできません。

GET の一覧／詳細は状態を変更しません。期限切れ Lease は `lease_expired=true`、`effective_state=ready` として読み取り、次の Claim Transaction が旧 Claim を `expired` にして Event を追加した後に新しい Claim を作成します。

Executor が成功 Result を送信した時点では Task は `submitted` です。対応する Canonical Artifact が既存の業務 API によって保存され、Change Automation が次工程を判定した場合だけ `completed` になります。Result 本文だけで業務状態を進めることはできません。

既定の `OrchestrationSchedulingPolicy.max_active_tasks_per_run` は `1` です。同じ Run に `claimed`、`running` または `submitted` Task がある間、別 Task は Claim できません。この設定が現在の単一 Agent 動作です。Web、Change Automation CLI、Task CLI は同じ `OPERAMIND_MAX_ACTIVE_TASKS_PER_RUN` 環境変数を読みます。将来はこの Deployment 設定と Capability ごとの Worker 数だけを変更し、Task Definition や Change Automation の業務判定を変更せずに複数 Subagent を有効化できます。

自動 Worker は `0048` の `orchestration_worker_registrations` に Executor Kind、ID、Capability、Project Scope、Worker 単位の並行数と期限付き Presence を登録します。自動 Claim は登録済み Capability と起動引数の一致を検査し、`max_concurrent_tasks` を超える Claim を拒否します。既定値は Worker ごとに `1`、Run ごとにも `1` です。複数 Worker は同時に別 Run を処理でき、同一 Run の並列化が必要な場合だけ Deployment 設定を明示的に増やします。人工 Claim はこの Worker 登録を要求しません。

`0049` 以降の Worker 登録は起動時に一度だけ返すランダム Credential Token の SHA-256 digest を保持します。Token の平文は DB、Monitoring API、画面、Event payload に保存しません。Agent／Subagent の Claim、Heartbeat、停止処理には登録時 Token が必要で、未登録 Executor、別 Worker の Token、Capability の偽装は拒否されます。`X-OperaMind-Worker-Token` Header または `OPERAMIND_WORKER_TOKEN` 環境変数を使い、Token をログや Task Result に書かないでください。

Worker の `status`（`online`／`draining`／`offline`）と `present`（期限内 Heartbeat）は別の状態です。`draining` は現在の Lease を完了させますが新しい Task を受け付けず、Heartbeat は Operator の status を上書きしません。画面または API の有効化、停止、ドレイン開始、Capability／並行上限変更はすべて `orchestration_worker_events` に Actor と時刻付きで追加されます。

Ready Queue は Task の `priority`（1～1000、値が大きいほど先）を第一キーにし、同じ優先度では直近一時間の Run ごとの Claim 回数が少ない Run を先に選びます。これにより重要度を保ちながら一つの Run による飢餓を抑えます。優先度の変更は Task Definition の digest を変えず、Ready／失敗／阻断 Task に対する Operator 操作として `priority_updated` Event を追加します。

## 人による確認

すべての Task は `agent`、`subagent`、`human` が同じプロトコルで Claim できます。ただし Claim した Executor の種類は承認 Evidence の代わりになりません。

Web の「人による Task 実行」では、現在 Task の Claim、Lease 更新、解放、Result 提出、失敗／阻断後の Requeue を同じ API で操作できます。加えて「OrchestrationTask 管理」では、Project 内または全 Project の複数 Run を横断し、Ready Queue、Capability、担当者、Lease、Result、依存関係、Event 履歴を確認できます。状態、Capability、阻断理由で絞り込み、選択した Task を人工 Claim、Release、Requeue できます。

「Task 実行モニタリング」は選択 Project と集計期間に対して、Queue 平均／P95 待機時間、平均実行時間、Result 成功率、再試行数、Lease 期限切れ数、頻出阻断理由を表示します。登録 Worker についてはオンライン判定、Capability、現在稼働数／並行上限、最終 Heartbeat を表示します。値が存在しない場合は `0` と未計測を区別します。

管理画面の SVG 依存関係図は Run ごとに前置 Task から後続 Task へ矢印を描き、各 Node に現在の `effective_state` を表示します。`blocked`／`failed` Node から未完了の子孫へ阻断を伝播表示しますが、Canonical Task State 自体は変更しません。Critical Path は実行時間の推測ではなく、現在保存されている依存 DAG の最長段数です。循環依存と取得上限外の前置 Task は、それぞれ警告 Node と「参照外」Node として fail-visible に表示します。

Lease Token はページの JavaScript メモリだけに保持し、DB、`localStorage`、`sessionStorage` には平文保存しません。Project を切り替えた場合、または表示中の Lease が期限切れになった場合は、ページメモリ内の Token も破棄します。

`confirm_requirement`、`confirm_document_diff`、`confirm_impact`、`issue_approval_grant` などの Judgment Task を `completed` にするには、既存の Web 確認操作によって生成された人工確認 Artifact Ref と受入 Evidence が必要です。Agent や Subagent は確認準備を実行できますが、自分を承認者として扱うことはできません。

## 自動 Worker

`operamind-orchestration-worker` は、起動時に Capability と並行上限を登録し、登録 Capability が Task の `required_capabilities` をすべて満たし、かつ管理者設定に固定 Action Handler が存在する Ready Task だけを自動 Claim します。Task の `instruction` や Model 出力を Shell Command として解釈しません。Handler は設定済みの argv を `shell=False` で起動し、Task は Lease Token を除いた JSON として標準入力へ渡します。

Worker は Claim 直後、実行中、および Result 登録直前に Heartbeat します。実行中の間隔は `--heartbeat-seconds` と Task Lease の三分の一の短い方です。Heartbeat が拒否された場合は Handler の Process Group を停止し、古い Token で Result を保存しません。期限切れ Claim は既存 Repository Transaction が `expired` にした後、別 Worker が同じ Task を新 Token で Claim できます。`SIGINT`／`SIGTERM` による正常停止では、有効な Lease を理由付き Release して Ready Queue に戻します。

Handler 設定例は `docs/examples/orchestration-worker-handlers.example.json` です。この例は同梱の `operamind-orchestration-generate-handler` を `generate_orchestration` Action に固定し、Canonical ChangeOrchestration、TestDataPlan、UI Plan を生成します。Handler の標準出力は次の一つの JSON Object に限定します。

```json
{
  "outcome": "completed",
  "summary": "Canonical Artifact を生成しました。",
  "artifact_refs": ["artifact-id"],
  "evidence": {"accepted": true}
}
```

`completed` は Artifact Ref と受入 Evidence が必須です。Handler の例外本文や標準エラーは Result に保存せず、分類済みの失敗種別だけを Evidence に残します。

## API

- `GET /api/v1/orchestration-tasks?run_id=...`: Run の Task、Dependency、Claim、Result、Event 履歴
- `GET /api/v1/orchestration-tasks/management`: 複数 Run の Task を Project、状態、Capability、阻断理由で絞り込む管理一覧
- `GET /api/v1/orchestration-tasks/graph`: Project／Run の Task 状態と Dependency を返す上限付き SVG Graph 投影
- `GET /api/v1/orchestration-tasks/monitoring`: Task 実行指標、頻出阻断理由、登録 Worker の稼働状態
- `GET /api/v1/orchestration-tasks/workers`: 登録 Worker、Presence、Capability、並行数、運用 Event 履歴（Credential Token は含まない）
- `PATCH /api/v1/orchestration-tasks/workers/{executor_kind}/{executor_id}`: Capability と Worker 並行上限を変更
- `POST /api/v1/orchestration-tasks/workers/{executor_kind}/{executor_id}/enable|disable|drain`: Worker を有効化、停止、ドレイン
- `GET /api/v1/orchestration-tasks/ready`: Executor Kind と Capability に一致する Ready Task
- `GET /api/v1/orchestration-tasks/{id}`: 一つの Task の Definition、Claim、Result、Event 履歴
- `POST /api/v1/orchestration-tasks/claim`: 次の Task を Claim
- `POST /api/v1/orchestration-tasks/{id}/claim`: Ready 一覧から選択した Task を精確に Claim
- `POST /api/v1/orchestration-tasks/{id}/heartbeat`: Lease 更新と `running` 遷移
- `POST /api/v1/orchestration-tasks/{id}/release`: 未完了 Task を解放
- `POST /api/v1/orchestration-tasks/{id}/result`: 成功、失敗、阻断結果と Evidence を登録
- `POST /api/v1/orchestration-tasks/{id}/requeue`: 理由付きで失敗／阻断 Task を明示的に再試行
- `PATCH /api/v1/orchestration-tasks/{id}/priority`: Ready／失敗／阻断 Task の Queue 優先度を変更

書き込み API の Executor ID は、任意の本文値ではなく信頼済み `X-OperaMind-Actor` Header から取得します。

Monitoring の `alerts` は `queue_backlog`（Ready 件数）、`task_timeout`（期限切れ Lease）、`queue_wait`（Queue P95 待機）を構造化して返します。`oldest_ready_wait_seconds` と併せて画面の閾値表示に使い、Alert 発生だけで Task の状態を自動変更しません。

## CLI

```bash
operamind-orchestration-task ready \
  --executor-kind agent \
  --capability impact_analysis

operamind-orchestration-task claim \
  --executor-kind agent \
  --executor-id operamind-single-agent \
  --capability impact_analysis

operamind-orchestration-task heartbeat \
  --task-id <task-id> \
  --executor-id operamind-single-agent \
  --lease-token <claim-response-token>

operamind-orchestration-worker \
  --handler-config docs/examples/orchestration-worker-handlers.example.json \
  --executor-id planning-worker-1 \
  --capability change_planning \
  --max-concurrent-tasks 1 \
  --heartbeat-seconds 10
```

結果登録では `--artifact-ref` と JSON Object の `--evidence-json` を指定します。Evidence は上限付きの Scalar Field に限定し、Source Code、文書本文、Diff 本文、Secret、Token、Password を示す Key は Repository 層で拒否します。
