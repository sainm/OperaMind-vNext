# P5 UI 検証とクローズ

## 実装済みの範囲

- `0012_ui_verification` は、承認済み Scenario、UI 環境、Deployment、Execution Plan、5 種類の Preflight、Run、Evidence、Scenario Result、Change Validation を正規化して保存します。
- Scenario は Project 単位でバージョン管理されます。`approved` のバージョンだけを active にでき、Plan は Edit Packet が要求する順序ですべての active Scenario と Evidence 要件を固定します。
- Deployment の Repository Revision は、範囲内・テスト成功・committed の Edit Result commit と完全一致しなければなりません。Plan 自身の Repository Revision にも同じ実 Git SHA を保存し、証拠状態名などの別フィールドで代用しません。
- Preflight は `environment`、`authentication`、`test_data`、`trigger_path`、`locator` を一つずつ必要とします。一つでも失敗または blocked の場合、Plan は実行できません。
- passed Scenario は、サニタイズ済み screenshot Evidence と assertion Evidence の両方を必要とします。Evidence は参照先と SHA-256 のみを DB に保存し、画像やログ本体は保存しません。
- Plan の全 Scenario に Result が一つずつ存在し、Packet の全 Impact Item が Evidence に紐づき、固定された Scenario Version の `evidence_requirements`（最低でも screenshot と assertion）をすべて満たした場合だけ `UiVerificationResult.status=passed` になります。どの Result からも参照されない余分な Evidence は拒否します。
- business assertion の失敗は Case を `failed`、範囲外ファイルは `reanalysis_required` にします。環境・認証・データ・Locator による blocked は Case を `verifying_ui` に保ち、同じ Ready Plan から再実行できます。
- Result Artifact、正規化結果、Evidence、Run/Plan、Analysis Case の更新は一つのトランザクションで確定し、同一 ID の完全重放だけを許可します。Plan の重放は入力 Scope だけでなく、Edit Result/Deployment の同一 Repository Revision、Packet の Scenario refs、固定 Scenario Version の正規化対応も再確認します。
- `0013_ui_browser_manifests` は、承認者、Browser/Viewport、Plan/Scenario Version、Trigger Path、Impact coverage、有限 Action/Assertion DSL、Screenshot mask Locator を固定します。保存済み header/spec から Manifest 全体を再構築して `payload_digest` を検証するため、正規化された Action、Assertion、Trigger Path、Locator の drift は実行前に停止します。
- Playwright Runner は Scenario ごとに独立した Browser Context を作成し、固定 sleep や任意 JavaScript を使わず、Locator の auto-wait と機械判定 Assertion を実行します。
- Local Evidence Store は approved root の下だけに PNG/JSON を保存し、DB には opaque ref と SHA-256 のみを書き込みます。ログは機密値を除去し、Screenshot は Manifest の redaction Locator を `mask` として撮影します。
- Executor の結果は信頼せず、Scenario、Plan の `execution_order`、固定 Scenario Version の Evidence 要件、Impact refs、Evidence 所有関係、サニタイズ済みフラグを照合します。不完全または越境した結果は completion の前に `blocked` 出力へ変換して閉じ、Run を `running` のまま残しません。
- `0014_ui_preflight_attempts` は Preflight を追加式 Attempt に変更します。blocked Attempt は履歴として保持し、環境を修復した後に新しい Attempt ID と Check ID で再確認できます。
- `0015_ui_knowledge` は UI Knowledge Snapshot を Project/Environment/Deployment Revision 単位で固定します。Snapshot header、Target、候補 Locator を決定的な順序で再構築し、毎回 `payload_digest` を再計算します。利用者は `stable_key` や CSS を直接編集せず、`ステータス絞り込み` のような業務名を持つ `target_ref` を Manifest から参照します。
- Locator candidate は strategy、priority、reliability score、出典を保持します。承認済み Snapshot のうち reliability threshold を満たす候補だけが実行時 Locator に解決されます。
- Browser Preflight は実ブラウザで origin、認証状態、テストデータ Assertion、Trigger Path、Locator の一意性と可視性を確認し、5 種類の Check を一つの追加式 Attempt として保存します。
- `propose-ui-knowledge` は Canonical `screen_element` の業務名、画面名、semantic/test attribute から決定的な draft candidate を生成します。内部の `screen_id` / `element_id` を利用者向け名称へ流用せず、不足項目は issue として返し、提案を自動承認しません。
- `0016_ui_locator_observations` と `observe-ui-knowledge` は、実 Deployment 上で候補の件数と可視性を確認し、`unique_visible`、`not_found`、`hidden`、`ambiguous`、`navigation_failed` を追加式 Run として保存します。Browser 観測後、保存前に source Snapshot と active/ready Deployment を再確認し、result Snapshot の ID/Version/Project/Environment/Deployment/draft 状態を要求値と照合します。Run の完全重放は run digest だけでなく、issues と各正規化 Observation row も比較します。`data-testid`、`aria-label`、明示 role、placeholder は有限 API だけで発見し、任意 JavaScript は実行しません。
- `0038_ui_knowledge_review_evidence` は、一意かつ可視だった業務目標の要素だけを実 Chromium で撮影し、Run／Observation／source Snapshot／Target に固定した追加式 Evidence として保存します。DB には path-confined な opaque ref、SHA-256、サニタイズ済みフラグだけを保存し、Web 配信時にも Project／result Snapshot の Scope、許可 Root、digest を再検証します。Screenshot を取得できない観測は `partial` として扱い、完全な Evidence に見せかけません。
- Runtime Observation は source Snapshot を変更せず、信頼度と provenance を更新した新しい `draft` Snapshot を作ります。観測済み candidate も QA の承認なしには active になりません。
- Browser 実行は Screenshot／Assertion／Step Log に加えて、network request、page navigation、form submission を区別したサニタイズ済み Route Observation を保存します。URL query、fragment、header、body、cookie、token は保存せず、内部 Manifest に静的 Route Ref がある場合だけ Code Graph との対応候補にできます。
- `0017_ui_knowledge_reviews` と `review-ui-knowledge` は、draft を直接更新せず、QA の判断を追加式 Review Event として保存し、新しい `approved` または `rejected` Snapshot を作成します。完全重放時も result Snapshot の正規化 Locator payload を再検証します。`approved` の場合だけ同一 Deployment の active Snapshot に切り替えられます。
- `0018_approval_grants` は UI Run を commit 後の `ui_pending` Grant に固定します。Grant の Scenario と Plan が完全一致し、期限切れ・取消済みでない場合だけ Run を開始でき、blocked は再試行可能、最終 closure は Grant を `completed` にします。
- Run 作成と非 blocked closure は Grant、Plan、Edit Result、Packet、Case、Deployment、Environment を同じ Transaction でロックまたは再検証して再認可します。`authorize_ui_plan` は Grant を先にロックしてから Plan をロックするため、読み取り後の Scenario 差し替えを許しません。開始後に Grant が失効、revoke、または Deployment/Environment/Plan source が stale になった Run は passed/failed を公開できず、明示的な `recover-run` だけが blocked として閉じられます。
- `0023_ui_plan_repository_binding` は Plan、committed Edit Result、Deployment の Repository Revision が同じ Git SHA であることを `verified` として固定します。旧実装で別フィールド値が保存された未完了 Plan/Run は `legacy_invalid` として blocked に隔離し、completed 履歴は改変しません。

## CLI

DB 接続は環境変数だけから読み取ります。

```bash
export OPERAMIND_DATABASE_URL='postgresql://...'
```

`operamind-ui` は一つの入口に 13 個の subcommand を持ちます。

```bash
operamind-ui register-scenario --help
operamind-ui build-plan --help
operamind-ui record-preflight --help
operamind-ui start-run --help
operamind-ui complete-run --help
operamind-ui recover-run --help
operamind-ui register-browser-manifest --help
operamind-ui register-ui-knowledge --help
operamind-ui propose-ui-knowledge --help
operamind-ui observe-ui-knowledge --help
operamind-ui review-ui-knowledge --help
operamind-ui preflight-browser --help
operamind-ui execute-browser --help
```

Scenario JSON は `scenario_id`、`title`、`preconditions`、`steps`、`expected_visible_results`、`evidence_requirements` を持つオブジェクトです。Golden Dataset の `evidence` フィールドも読み取れます。

```bash
operamind-ui register-scenario \
  --scenario scenario.json \
  --scenario-version-id scenario-version-001 \
  --scenario-version v1 \
  --project-id visiondemo \
  --trigger-path /expenses \
  --data-recipe-ref expense-seed-v1 \
  --review-status approved \
  --activate
```

Plan は Deployment と Edit Result の commit を照合して作成します。

```bash
operamind-ui build-plan \
  --plan-id ui-plan-001 \
  --project-id visiondemo \
  --analysis-case-id analysis-case-001 \
  --edit-packet-id edit-packet-001 \
  --edit-result-id edit-result-committed-001 \
  --environment-id visiondemo-local \
  --base-url http://127.0.0.1:8080 \
  --deployment-revision build-001 \
  --repository-revision commit-after-edit-001
```

通常は `preflight-browser` を使い、実ブラウザから 5 種類の Check を自動作成します。

```bash
operamind-ui preflight-browser \
  --project-id visiondemo \
  --plan-id ui-plan-001 \
  --manifest-id browser-manifest-001 \
  --attempt-id preflight-attempt-001 \
  --storage-state /secure/path/storage-state.json
```

外部の検証基盤が作成した結果を取り込む場合に限り、Preflight JSON を次の 5 オブジェクトとして `record-preflight` に渡せます。失敗または blocked の項目には `reason` が必要です。

```json
[
  {"check_id":"check-env","check_type":"environment","status":"passed","evidence_ref":"health://ok"},
  {"check_id":"check-auth","check_type":"authentication","status":"passed"},
  {"check_id":"check-data","check_type":"test_data","status":"passed"},
  {"check_id":"check-path","check_type":"trigger_path","status":"passed"},
  {"check_id":"check-locator","check_type":"locator","status":"passed"}
]
```

```bash
operamind-ui record-preflight \
  --project-id visiondemo \
  --plan-id ui-plan-001 \
  --attempt-id preflight-attempt-001 \
  --checks preflight.json

operamind-ui start-run \
  --project-id visiondemo \
  --plan-id ui-plan-001 \
  --run-id ui-run-001 \
  --approval-grant-id approval-grant-001
```

`complete-run` は Scenario Result 配列と Evidence 配列を受け取ります。Scenario の `evidence_refs` は Evidence の `evidence_id` を参照します。

```bash
operamind-ui complete-run \
  --verification-result-id verification-result-001 \
  --project-id visiondemo \
  --run-id ui-run-001 \
  --scenario-results scenario-results.json \
  --evidence evidence.json
```

passed は終了コード `0`、記録された failed/blocked/reanalysis_required は `1` を返します。

Browser worker が強制終了されて Run が `running` のまま残った場合は、DB を直接更新せず `recover-run` で閉じます。`--stale-before` は timezone 付きの固定時刻で、Run の `started_at` がこの境界以前かつ境界自体が未来でない場合だけ実行できます。Recovery ID は Verification Result ID と同じ値にし、操作人と理由を含む不変な `UiVerificationResult(status=blocked)` を保存します。Plan と UI-pending Grant は再試行可能なままです。

```bash
operamind-ui recover-run \
  --verification-result-id ui-recovery-001 \
  --project-id visiondemo \
  --run-id ui-run-interrupted-001 \
  --recovery-id ui-recovery-001 \
  --actor operator@example.com \
  --reason 'browser worker process was interrupted' \
  --stale-before 2026-07-16T12:00:00Z
```

## Browser Manifest と実行

自然言語の Scenario はそのまま実行しません。QA が Deployment に対する UI Knowledge と Plan に対する構造化 Manifest を確認し、`approved` と承認者を記録します。完全な例は `docs/examples/ui-knowledge-snapshot.v1.json` と `docs/examples/ui-browser-manifest.v1.json` にあります。

```bash
operamind-ui propose-ui-knowledge \
  --project-id visiondemo \
  --document-snapshot-id document-snapshot-after-001 \
  --environment-id visiondemo-local \
  --deployment-revision build-001 \
  --snapshot-id ui-knowledge-proposal-001 \
  --snapshot-version proposal-1

operamind-ui register-ui-knowledge \
  --snapshot docs/examples/ui-knowledge-snapshot.v1.json
```

提案結果は常に `draft` です。`business_name_missing`、`screen_name_missing`、`semantic_locator_review_required` などの issue を解消し、QA が候補と runtime evidence を確認してから、別の approved Snapshot として登録します。

提案 Snapshot を DB に登録した後、実 Deployment で観測して次の draft version を生成します。

```bash
operamind-ui observe-ui-knowledge \
  --project-id visiondemo \
  --source-snapshot-id ui-knowledge-proposal-001 \
  --observation-run-id ui-observation-run-001 \
  --result-snapshot-id ui-knowledge-observed-001 \
  --result-snapshot-version 1.0.1-draft \
  --storage-state /secure/path/storage-state.json
```

観測結果を QA が確認した後、承認結果は新しい不変 Snapshot として記録します。`--activate` は `approved` の場合だけ指定できます。

同じ操作は日本語の Web コントロールプレーンからも行えます。Project を選ぶと未確認 draft が表示され、画面名と業務目標、候補 strategy／値／出典、信頼度、一致数／可視数、観測 issue、Evidence Screenshot を比較できます。確認者と問題・判断理由を入力して承認または却下すると、サーバーが Review Event と新しい Snapshot ID を生成します。元の draft、観測 Run、Screenshot は履歴として保持され、Version 履歴には決定と理由が表示されます。

```bash
operamind-ui review-ui-knowledge \
  --project-id visiondemo \
  --source-snapshot-id ui-knowledge-observed-001 \
  --review-event-id ui-knowledge-review-001 \
  --result-snapshot-id ui-knowledge-approved-001 \
  --result-snapshot-version 1.0.1 \
  --decision approved \
  --reviewed-by qa@example.com \
  --reason 'runtime observation reviewed' \
  --activate
```

許可される Locator は `role`、`label`、`text`、`test_id`、`placeholder`、安定した単一 `css` です。CSS の深い階層、XPath、任意 JavaScript は拒否されます。Action は `click`、`fill`、`select_option`、`check`、`uncheck`、Assertion は visible/hidden/text/value/count/checked 系に限定されます。

```bash
operamind-ui register-browser-manifest \
  --manifest docs/examples/ui-browser-manifest.v1.json

operamind-ui execute-browser \
  --project-id visiondemo \
  --plan-id ui-plan-001 \
  --manifest-id browser-manifest-001 \
  --run-id ui-run-001 \
  --verification-result-id verification-result-001 \
  --approval-grant-id approval-grant-001 \
  --evidence-root /absolute/path/to/ui-evidence \
  --storage-state /secure/path/storage-state.json
```

`--storage-state` は任意です。認証情報は DB や Manifest に保存せず、Playwright の storage state または `OPERAMIND_UI_` prefix の環境変数からのみ参照します。Action/Assertion の値や storage state の内容は Evidence に出力しません。

## 検証済みの境界

PostgreSQL の空 Schema migration、checksum、既存 Preflight row の Attempt バックフィル、blocked Preflight の再試行、Scenario の不変性、Deployment commit 一致、Plan の完全 Scope 重放と source invalidation、Run の静的 identity、UI Knowledge/Locator と Browser Manifest/spec の digest drift、UI Knowledge の Deployment 固定、Runtime Observation と Screenshot Evidence の完全重放、新 draft version、Review Event の完全重放と active approved Snapshot、Manifest の完全 coverage/trigger/version 固定、Plan 順序と Evidence 要件に反する Executor 出力の拒否、Evidence のサニタイズと digest、Artifact の完全重放、Case の `verifying_ui -> passed` 遷移を自動テストしています。`OPERAMIND_PLAYWRIGHT_LIVE=1` の live test はローカル HTTP target と実 Chromium を使い、Browser Preflight、runtime `data-testid` discovery、要素 Screenshot、日文 Web 画面での一致数／信頼度表示、approve/reject による新 Version 生成、成功、business assertion 失敗を確認します。

## 未実装の境界

- S3 などの共有 Object Storage Evidence adapter。現在は path-confined Local Evidence Store を実装しています。
- Approval Grant の Web UI、UI 結果を書き込む MCP adapter、および Golden Dataset の実 target deployment を使った E2E。UI Plan と Validation Result の有界 read-only MCP query は実装済みです。
