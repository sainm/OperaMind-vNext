# Web UI 分割与変更行カバレッジ

## Web UI の責務分割

`src/operamind/web/static/app.js` は画面の状態遷移と API 呼び出しを担当し、表示用の業務判断は次の独立モジュールに分けています。

- `change-management.js`: 変更範囲、テストデータフロー、UI Scenario、業務ルールの表示モデル。
- `test-data-management.js`: 画面横断データの変数、画面、操作、Assertion の日本語表示。
- `case-editor.js`: 自然言語 Case 修正の field label、Proposal 正規化、曖昧性選択の検証。
- `verification-results.js`: Business Coverage、Changed-line Coverage、Closure とブロック理由の表示モデル。
- `traceability-view.js`: 設計変更、影響項目、影響コード、検証基準、Test Case、テストデータ、UI 検証、Coverage、Edit Result、Closure Result の関係表示モデル。

各モジュールはブラウザのグローバル API と Node `require` の両方に対応し、`tests/web/test_frontend_modules.py` で日本語表示と純粋な表示モデルを検証します。画面のすべてのユーザー向けラベルは日本語を返し、内部 ID は証跡の識別にだけ使用します。

## 変更トレーサビリティと不足台帳

`GET /api/v1/change-requests/{request_id}/traceability` は、1 件の変更要件を起点に Canonical Artifact 間のノードと関係を返します。Web はこの結果を工程列として表示し、`gaps` に必須関係の欠落を集約します。影響項目が設計変更に結び付かない、コード範囲・Test Case・TestDataPlan・UI 検証・Committed Edit Result・Closure Result が欠ける場合は欠落として明示され、Closure の `unresolved_items` も同じ画面で確認できます。

この台帳は表示専用です。画面から Artifact を直接書き換えず、修正は既存のレビュー、承認、実行、証跡登録 API を通じて行います。ノードが揃っていても、実行結果や Evidence が存在しない場合は自動的に「不足」になります。

## 変更行カバレッジ

コミット済み Edit Result は、承認済みテストコマンドの結果を `changed_line_coverage` として受け取ります。OperaMind は Git の追加・変更行、カバレッジツールの実行可能行、実行済み行を交差させ、次を不変の `ChangedLineCoverageReport` として保存します。

- `changed_line_count` / `covered_changed_line_count`
- `coverage_percent` と `minimum_coverage_percent`（既定 80%）
- ファイルごとの未カバー変更行
- 参照した Command Evidence
- `passed`、`failed`、`missing`、`not_required` の判定

ソース変更に証跡がない場合は、Working／Committed のどちらも Git の変更行を未カバー候補として記録し、`missing` にします。しきい値未満は `failed` です。どちらも `ChangeClosureResult` を `blocked` にし、`unresolved_items` に理由を追加します。ドキュメントだけの変更は `not_required` です。Business Coverage は従来どおり別の業務ルール単位で計算され、Closure には両方のパーセントと状態が表示されます。

Closure が参照できる Edit Result は、同じ Project／Case だけではなく、現在の Orchestration と同じ Impact Report から作られた Edit Packet に限定されます。Changed-line Coverage の Base／Result Revision もその Edit Result と一致しなければなりません。これにより、別の変更や古い Coverage を現在の Closure に流用できません。

Business Coverage の Closure 判定値は、Report の集計値をそのまま信用せず、業務ルール明細の `covered` 状態から再計算します。明細、件数、パーセント、Status が一致しない場合も Closure をブロックします。変更行 Coverage の証跡不足、対象ファイル不足、基準未達、未カバー行、および Business Coverage の未カバールール／集計不整合は日文画面に具体的な理由として表示されます。

変更行 Coverage のシステム最低基準は 80% です。CLI／MCP の入力はより厳しい値にできますが、80% 未満には下げられません。現在の Edit Result と Artifact 参照が一致しない古い Closure は Web の現在判定から除外され、再生成が必要な理由を日文表示します。

CLI では次の形式の JSON を `--changed-line-coverage` に渡せます。MCP の `copilot_record_edit_result` と `copilot_record_task_result` も同じ構造を受け付けます。

```json
{
  "evidence_refs": ["command-execution-id"],
  "executable_lines": {"src/service.py": [10, 11, 12]},
  "covered_lines": {"src/service.py": [10, 12]},
  "minimum_coverage_percent": 80
}
```
