# P5 テストデータ、UI 検証、クローズ

## 実行順序

UI テストはコード変更前やテストデータ生成前には実行しません。

1. Copilot がコードを変更する。
2. `copilot_validate_task_diff` が作業差分を Packet 内と確認する。
3. Copilot が固定コンパイル／テスト／カバレッジコマンドを実行し、変更を commit する。
4. 同じ content digest に対する全コマンドが成功した後、Copilot が変更要件、設計書差分、検証済みコード差分から自然言語 `UiTestPlan`（実際のブラウザ画面で実行する計画）と実行可能 `TestDataPlan` を生成する。
5. OperaMind が TestDataPlan の HTTP / SQL / UI Step を依存順に実行して画面横断データを生成し、登録済み DataIdentityProvider で対象レコードを一意に固定する。
6. 同じ Flow の有限 UI Step / UI Assertion を実ブラウザで実行し、サニタイズ済み Screenshot を保存する。
7. `TestDataExecutionResult`、`UiVerificationResult`、`ChangeClosureResult` を順に生成する。

## UiTestPlan / TestDataPlan 門禁

`output_stage=test_planning` は committed Diff が `in_scope` になり、同一 content digest に対する必須コンパイル／テスト／カバレッジコマンドと、OperaMind が Coverage report から算出した変更行 Coverage が成功した後だけ受理します。ここでの TestPlan は API やソース検査の計画ではなく、実画面を操作する `schema_version=v2, plan_kind=ui` の UiTestPlan です。正式生成および Locator 修正版の TestDataPlan は RunContext と Run 固有 Binding を持つ `schema_version=v3` に統一し、既存 v1／v2 は履歴読取だけを維持します。

- Test Case ID と Test Data ID は一意である。
- すべての Test Case は存在する Test Data を参照する。
- Generation Flow は TestPlan の Case を過不足なく覆う。
- 各自然言語 Step は並行する一意な `step_id` を持ち、同じ Case の少なくとも一つの `channel=ui` Step から `test_step_refs` で参照される。
- `test_step_refs` を持つ Step は限定 Playwright Action を必須とし、Flow 外の Case や存在しない自然言語 Step を参照できない。
- UI Case は `execution_mode=browser` で、各 UI Step に限定 Playwright Action と UI Assertion を持つ。
- Playwright は登録された同一 Origin だけを操作し、各 UI Step の Screenshot と Step Log を Evidence として保存する。
- `ui_impact=true` の Scope がある場合、TestPlan に UI Case が必要である。
- 各 UI Case は少なくとも一つの `channel=ui` Step と `observe_via=ui` Assertion を持つ Flow に対応する。
- `status=blocked` の Plan は実行しない。

自然言語の UI 手順をそのまま任意操作として評価しません。自然言語 Step と実行 Step の対応を先に検証し、実行対象は TestDataPlan Schema が許可する `screen_ref`、`ui_action_ref`、Playwright Action、入力、有限 Assertion だけです。対応する実行 Step、Binding、許可された Base URL、または一意な Locator がない場合は fail closed にします。

Playwright DSL は画面遷移、再読込、前進／後退、クリック／ダブルクリック、入力／追加入力／クリア、選択、チェック、キー操作、hover、focus／blur、要素へのスクロール、drag、要素／URL／Load State 待機、同一 Origin iframe を扱います。Locator は role、label、placeholder、text、alt text、title、test id、CSS を利用でき、観測は text、count、visible、enabled、checked、value、attribute を扱います。

Copilot は画面設計書、HTML／Template／Frontend Source、Route、Code Graph、Test Case、TestDataPlan、DataIdentityProvider の業務 Identity 定義から Locator を設計します。画面ごとに `test_id`、`role + accessible name`、`label`、`placeholder`、`title`、`alt_text`、安全な CSS を選択でき、全画面に共通の ID や表構造を要求しません。複数の `screen_identity_values` が必要なレコードは Locator の `all` に全条件を含め、一条件だけを任意採用しません。`nth()`、`nth-child`、行番号、曖昧 Text、座標、未検証の動的 CSS は Plan 確認前に拒否します。

Plan 確認前の独立 Browser 予行 Run は作りません。正式 UI Test Run が一つの Browser Context を開き、その同一 Session で対象画面へ移動し、各状態変更 Action の直前に `pre_action_observations` の期待値を実 DOM から読み取ります。Origin、対象 Locator の唯一性、確認済み画面状態のいずれかが不一致なら Action を実行しません。`bound_record` ではこれに加えて現在 Project／Run の frozen Binding、record Scope count=1、全業務 Identity 値、`observed_identity_digest`、Scope 内 Action count=1 を確認します。

`bound_record` Step は Action より先に frozen screen Locator の count=1 を確認します。次に同じ container 内の exact `dom_observation` から業務一意キーと画面キーの実値を読み、OperaMind が `observed_identity_digest` を計算して frozen identity digest と比較します。0 件、複数件、値欠落、不一致は Action を一度も実行せず日本語の阻断理由を返します。期待 digest または Binding content digest を observed 値として渡す Adapter は拒否します。相対 DOM Locator は同一 container 内に限定し、iframe は使用できません。この RunContext／逐 Step Binding Evidence を持つ実行結果は `TestDataExecutionResult v3` とし、既存 v1／v2 Artifact は変更せず履歴として読み取れます。

Locator が 0 件／複数件、業務 Identity 欠落／不一致、Scope 外 Action、DOM drift、Origin 越境の場合は、対象 Action を実行せず、脱敏 Screenshot、Step Log、Locator 種別、各 match count、公開可能な Observation、失敗工程を blocked Evidence として保存します。この Evidence は同じ Copilot Change Task に返し、OperaMind が自動で read-only `ui_test_plan_revision` Task を作成します。Copilot は業務期待値を変えずに完全な新しい Plan Revision を生成します。Revision Task の発行自体が失敗した場合は Run の blocked Evidence を失わず `locator_revision_publish_failed` を記録し、成功扱いにも手動 Locator 推測にも切り替えません。新 Revision は Schema／安全検証と同じ Confirmation API による最終人工確認を再度通し、別 Run として開始します。失敗した Run の途中で Locator を差し替えて再開しません。

Canvas、DOM から認識できない仮想 UI、OS Native Dialog などで Playwright が明示的な `PlaywrightCapabilityError` になった場合だけ、確認済み `computer_use_fallback` を利用できます。Fallback は自然言語 Objective、理由、最大操作数、観測項目を Plan に固定し、同一 Origin、Screenshot、Action Kind Log を必須にします。AI は操作と Action Kind だけを返し、URL、業務 Observation、Screenshot は同じ受控制 Playwright Session から独立に再取得して Assertion に使用します。`bound_record`、Locator 0 件／複数件、Identity 不一致、単なる Locator 誤りには Fallback を使用しません。Fallback 後も Playwright が Observation と Screenshot を再取得し、AI の成功申告だけを Evidence にしません。AI Computer Use Provider は注入式で、未設定の標準環境では自動操作せず fail closed になります。

## データと UI Evidence

`identity_binding.provider` は登録済みの `database`、`api`、`ui`、`hybrid` のいずれかを明示します。Provider は実観測とサニタイズ済み source Evidence を必要とし、`primary_key`、`business_unique_keys`、`screen_identity_values`、`record_scope_locator`、`match_count`、`evidence_ref` を返します。`match_count != 1`、Provider 未登録、必要 Evidence 欠落、fake／推測／静かな fallback は blocked です。Secret は Data Identity、DB、ログ、Copilot Context、Evidence に保存しません。

TestData 実行は Flow / Step / Phase ごとに進捗を保存します。失敗後も指定された cleanup を逆順に試行し、値そのものは進捗イベントへ書きません。Binding 付き UI cleanup は操作前の Frozen Scope=1 と DOM Identity を再確認し、操作後に同じ Scope が `cleanup_record_scope_match_count=0` になったことを実 DOM で証明します。Database／API Source がある Binding は、その Source に対応する確認済み cleanup readback でも 0 件を証明しなければ成功しません。

UI Case が passed になるには次が必要です。

- 対応 Flow が `passed`
- すべての UI Step が `passed`
- UI Assertion が成功
- UI Step に結び付いたサニタイズ済み Screenshot Evidence が一件以上

各 UI Step の Result は `driver`、実 Locator 種別、record Scope／Action の match count、実 `screen_identity_values`、`observed_identity_digest`、Assertion、Step Log、Screenshot、実際に使用した Binding ref を保持します。普通利用者向け Web／Chat には操作目的、対象業務データ、画面、操作、検証結果、阻断理由、次の Action だけを表示し、Locator JSON、digest、`stable_key`、内部 Artifact ID、Raw DOM、MCP Raw I/O は「詳細」または Evidence に限定します。

条件を満たす場合だけ OperaMind が Case-scoped `UiVerificationResult(status=passed)` を生成します。Copilot の自己申告や通常の unit-test command を UI Evidence として流用しません。

## 最終レポート

`ChangeClosureResult` は次を同じ Change Request / Case / Revision に結び付けます。

- ChangeRequest
- StructuredChange
- ImpactReport
- committed EditResult と Command Evidence
- changed-line coverage
- TestPlan / TestDataPlan
- TestDataExecutionResult
- UiVerificationResult（UI Case がある場合）
- BusinessCoverageReport

一つでも欠落、失敗、範囲外、Revision drift があれば `passed` にしません。UI Case がない変更だけは `ui_status=not_impacted` として閉じられます。

## Web 表示

Web の `ui_validation` は UiTestPlan の自然言語手順、跨画面 TestDataPlan、入力・出力変数、事後／最終 Assertion、cleanup、実行状態、Screenshot を表示します。生成済み計画は Web から自然言語で修正できます。確認すると旧計画を直接書き換えず、確認済みの変更を VS Code GitHub Copilot の read-only UI TestPlan revision Task として渡します。Copilot が完全な UiTestPlan と TestDataPlan を再生成し、OperaMind が検証・保存した時点で初めて新しい Version の実ブラウザ実行を開始します。その瞬間に旧 TestData Run、実行 Artifact、Screenshot、Closure、最終レポートは stale となり、現在 Version から再利用できません。`final_report` は business coverage、changed-line coverage、変更 Path、Case ごとの結果、UI 状態、未解決項目と阻断理由を表示します。
