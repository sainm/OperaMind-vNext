# P4 コード影響範囲と安全な変更

## 主フロー

P4 は、確定した設計書差分からコード変更可能範囲を作り、同じ VS Code GitHub Copilot Change Task に返す工程です。利用者が Impact Item、Edit Packet、Approval Grant を個別操作する Web 画面はありません。

1. Copilot が `output_stage=document_change` を記録する。
2. OperaMind が実ファイルと Canonical Snapshot を比較し、`StructuredChange` を生成する。
3. Copilot がコードを読み取り専用で調査し、`output_stage=code_scope` を記録する。
4. OperaMind が現在 Git Revision の Code Graph を再構築し、Path、Symbol、Test Binding、Action、UI 影響を検証する。
5. Web または VS Code で利用者が現在の `ImpactReport` を確認する。
6. OperaMind が確認済み範囲から `EditPacket` と内部実行範囲を自動生成する。
7. Copilot は返された Packet の `editable_files` / `test_files` だけを変更する。

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

`ImpactConfirmation` は Web と VS Code で共有する人工確認から生成します。`EditPacket` と `ApprovalGrant` は、確認済みの同一 Evidence Digest から OperaMind が自動生成します。対象 Evidence が変わった場合、以前の確認は自動的に無効になります。次の場合は該当する六工程を blocked にします。

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
- 現在 Code Graph の関係から限定した変更対象、依存ファイル、関連テストのファイルグラフ
- 選択したファイルの Language、Role、対象 Symbol、影響理由、関連テスト
- コード変更と Git Diff の状態
- コンパイル・テスト結果
- 変更後 Revision と coverage
- 業務上理解できる阻断理由

グラフは Impact Report の直接対象と関連テスト、および現在 Code Graph で接続する一段階の依存だけを表示します。40 ファイルを超える場合は表示件数を明示して限定し、未解決・外部 Edge を確定関係として描画しません。Grant ID、Lease、Worker、内部 Task は表示・公開せず、利用者は六工程共通の確認だけを操作します。
