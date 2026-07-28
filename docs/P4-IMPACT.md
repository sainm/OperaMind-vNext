# P4 コード影響範囲と安全な変更

## 主フロー

P4 は、確定した設計書差分からコード変更可能範囲を作り、同じ VS Code GitHub Copilot Change Task に返す工程です。利用者が Impact Item、Edit Packet、Approval Grant を個別操作する Web 画面はありません。

1. Copilot が `output_stage=document_change` を記録する。
2. OperaMind が実ファイルと Canonical Snapshot を比較し、`StructuredChange` を生成する。
3. Copilot がコードを読み取り専用で調査し、`output_stage=code_scope` を記録する。
4. OperaMind が現在 Git Revision の Code Graph を再構築し、Path、Symbol、Test Binding、Action、UI 影響を検証する。
5. 確定的な `ImpactReport` を自動確認し、`EditPacket` と内部実行範囲を生成する。
6. Copilot は返された Packet の `editable_files` / `test_files` だけを変更する。

## Code Scope 契約

各項目は次を持ちます。

- `target_path`
- `target_symbols`
- `recommended_action`: `modify` / `add` / `delete` / `review_only`
- `test_file_refs`
- `rationale`
- `ui_impact`

既存 Path は Code Graph に存在し、Symbol は同じ File に属する必要があります。`add` は Profile の `default_scan_roots` 内かつ対応 Language の拡張子だけを許可し、まだ存在しない Symbol を事前に主張できません。新しい Test Path は Test と判定できる名称、許可 Root、許可 Language をすべて満たす必要があります。

## 内部認可

`ImpactConfirmation`、`EditPacket`、`ApprovalGrant` は安全境界として保持しますが、確定的な主フローでは OperaMind が自動生成します。次の場合だけ該当する六工程を blocked にします。

- 文書差分に unknown または低 confidence がある
- Code Graph が不完全、Profile が一意でない、Revision が一致しない
- Copilot Scope が Graph 外、Packet 外、または Test Binding を欠く
- コマンドが固定 `CommandExecutionProfile` にない
- Git Diff が Packet 外へ出る

## Copilot 実行

主フローで Copilot が使う MCP Tool は次の五つです。

1. `copilot_get_coding_task`
2. `copilot_record_change_outputs`
3. `copilot_validate_task_diff`
4. `copilot_run_task_command`
5. `copilot_record_task_result`

任意 Shell は渡しません。コマンドは Profile の固定 `argv` と timeout で実行し、結果は exit code と digest-based Evidence だけを Task に結び付けます。committed Result は現在 Packet、Grant、Repository common-dir、Revision、Command Evidence、changed-line coverage を再検証します。

## Web 表示

Web は `code_scope` と `compile_test` に以下だけを表示します。

- 対象 Path / Symbol / Test Binding
- コード変更と Git Diff の状態
- コンパイル・テスト結果
- 変更後 Revision と coverage
- 業務上理解できる阻断理由

Grant ID、Lease、Worker、内部 Task、手動確認ボタンは表示・公開しません。
