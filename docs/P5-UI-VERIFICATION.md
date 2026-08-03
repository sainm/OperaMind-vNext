# P5 テストデータ、UI 検証、クローズ

## 実行順序

UI テストはコード変更前やテストデータ生成前には実行しません。

1. Copilot がコードを変更する。
2. `copilot_validate_task_diff` が作業差分を Packet 内と確認する。
3. コードのコンパイル・テスト・必要なカバレッジコマンドがすべて成功した後、Copilot が変更要件、設計書差分、検証済みコード差分から自然言語 `UiTestPlan`（実際のブラウザ画面で実行する計画）と実行可能 `TestDataPlan` を生成する。
4. Copilot が固定コンパイル／テストコマンドを実行し、変更を commit する。
5. OperaMind が TestDataPlan の Fixture / HTTP / SQL Step を依存順に実行して画面横断データを生成する。
6. 同じ Flow の有限 UI Step / UI Assertion を実ブラウザで実行し、サニタイズ済み Screenshot を保存する。
7. `TestDataExecutionResult`、`UiVerificationResult`、`ChangeClosureResult` を順に生成する。

## UiTestPlan / TestDataPlan 門禁

`output_stage=test_planning` は committed Diff が `in_scope` になり、同一 content digest に対する必須コンパイル／テスト／カバレッジコマンドと、OperaMind が Coverage report から算出した変更行 Coverage が成功した後だけ受理します。ここでの TestPlan は API やソース検査の計画ではなく、実画面を操作する `schema_version=v2, plan_kind=ui` の UiTestPlan です。

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

Canvas、DOM から認識できない仮想 UI、OS Native Dialog などで Playwright が明示的な `PlaywrightCapabilityError` になった場合だけ、確認済み `computer_use_fallback` を利用できます。Fallback は自然言語 Objective、理由、最大操作数、観測項目を Plan に固定し、同一 Origin、Screenshot、Action Kind Log を必須にします。AI は操作と Action Kind だけを返し、URL、業務 Observation、Screenshot は同じ受控制 Playwright Session から独立に再取得して Assertion に使用します。業務 Assertion、認証、通信、跨 Origin の失敗は Fallback 対象ではありません。AI Computer Use Provider は注入式で、未設定の標準環境では自動操作せず fail closed になります。

## データと UI Evidence

TestData 実行は Flow / Step / Phase ごとに進捗を保存します。失敗後も指定された cleanup を逆順に試行し、値そのものは進捗イベントへ書きません。

UI Case が passed になるには次が必要です。

- 対応 Flow が `passed`
- すべての UI Step が `passed`
- UI Assertion が成功
- UI Step に結び付いたサニタイズ済み Screenshot Evidence が一件以上

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
