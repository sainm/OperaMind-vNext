# P5 テストデータ、UI 検証、クローズ

## 実行順序

UI テストはコード変更前やテストデータ生成前には実行しません。

1. Copilot がコードを変更する。
2. `copilot_validate_task_diff` が作業差分を Packet 内と確認する。
3. Copilot が変更要件、設計書差分、検証済みコード差分から自然言語 `TestPlan` と実行可能 `TestDataPlan` を生成する。
4. Copilot が固定コンパイル／テストコマンドを実行し、変更を commit する。
5. OperaMind が TestDataPlan の Fixture / HTTP / SQL Step を依存順に実行して画面横断データを生成する。
6. 同じ Flow の有限 UI Step / UI Assertion を実ブラウザで実行し、サニタイズ済み Screenshot を保存する。
7. `TestDataExecutionResult`、`UiVerificationResult`、`ChangeClosureResult` を順に生成する。

## TestPlan / TestDataPlan 門禁

`output_stage=test_planning` は working-tree Diff が `in_scope` になった後だけ受理します。

- Test Case ID と Test Data ID は一意である。
- すべての Test Case は存在する Test Data を参照する。
- Generation Flow は TestPlan の Case を過不足なく覆う。
- `ui_impact=true` の Scope がある場合、TestPlan に UI Case が必要である。
- 各 UI Case は少なくとも一つの `channel=ui` Step と `observe_via=ui` Assertion を持つ Flow に対応する。
- `status=blocked` の Plan は実行しない。

自然言語の UI 手順をそのまま任意操作として評価しません。実行対象は TestDataPlan Schema が許可する `screen_ref`、`ui_action_ref`、入力、有限 Assertion だけです。Binding がない、Base URL が許可されない、Locator が一意でない場合は fail closed にします。

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

Web の `compile_test` は自然言語 Test Case の事前条件、手順、期待結果を表示します。生成後の修正は同じ六工程内で自然言語を入力し、構造化された変更前／変更後と必要な選択肢を一度確認してから適用します。確定的な修正もプレビューを省略せず、確認後に TestPlan、TestDataPlan、Coverage と下流実行を新しい Version として再生成します。`ui_validation` は TestDataPlan のデータ生成／UI Step、入力・出力変数名、事後／最終 Assertion、cleanup、実行状態、Screenshot を同じ六工程の中に表示します。`final_report` は business coverage、changed-line coverage、変更 Path、Case ごとの結果、UI 状態、未解決項目と阻断理由を表示します。値を結び付ける内部 Artifact／Flow／Step／Assertion ID、Grant、Queue、Lease は表示しません。旧 UI Knowledge、Browser Manifest、Preflight、Run、Recovery の管理画面、手動 UI／Closure CLI と production table query は主フローから削除済みです。既存 DB の Migration と不可変 Artifact／Closure の歴史読み取りだけを維持し、新しい Change Request では旧管線の Record を作成しません。
