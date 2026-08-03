# OperaMind 空 Database 手動 E2E 実行記録（2026-08-02）

## 1. 実行範囲

`docs/MANUAL-E2E-TEST.md` Section 14 に従い、空 Database、Git 管理外コード Workspace、既存 Git 管理の設計書 Root から主変更フローを実行する。

| 項目 | 実値 |
|---|---|
| 開始日時 | 2026-08-02 03:19 CST より前 |
| OS／Architecture | macOS 26.5.2／arm64 |
| 起動入口 | source developer mode |
| OperaMind Root | `/Users/sainm/work/github/OperaMind-vNext` |
| Database | local PostgreSQL `operamind_vnext` |
| Code Workspace | `/Users/sainm/work/github/VisionDemo` |
| Document Root | `/Users/sainm/work/github/OperaMind-demo/design-docs` |
| Project ID | `visiondemo-manual-e2e-20260802` |
| Project 名 | `VisionDemo 手動 E2E 20260802` |
| UI Test URL | `http://127.0.0.1:8080` |
| Current Change ID | `expense-status-all-20260802-04` |
| Code baseline revision | `a6b055e` |
| Document baseline revision | `ad23d0a7` |

Requirement:

```text
経費精算申請一覧でステータス「すべて」を選択した場合、申請状態で絞り込まず全件を表示する。
「申請中」と「差戻し」を選択した場合の既存検索動作は変更しない。
一覧の検索結果件数と選択状態を実ブラウザで確認する。
```

## 2. Database 初期化結果

- Web を停止して `public` Schema を Drop／Create した。
- Migration `0001` から `0069` までを新規適用した。
- Migration 直後の次の Table はすべて 0 件だった。
  - `projects`
  - `change_requests`
  - `documents`
  - `search_index_builds`
  - `document_search_vectors`
  - `artifact_records`
  - `change_automation_runs`
  - `copilot_coding_tasks`
- Web は Login を要求せず、登録済み Project 0 件の初期画面を表示した。

結果: passed

## 3. Project 初期化結果

- Web の `新しいプロジェクト` から Project を登録した。
- Git 管理外だった Code Workspace に OperaMind local Git が作成された。
- 初回 Commit は `a6b055e OperaMind local baseline` で、作成直後の worktree は clean だった。
- 既存 Git 管理の Document Root は Commit `ad23d0a7` を再利用した。
- Canonical 文書 14 件、Snapshot 1 件、Search Index 1 件、RAG Vector 161 件を生成した。
- UI Test URL は Project Summary に表示された。

結果: passed

補足: 初回実行時、Web 成功通知に Canonical 文書数と Vector 数が表示されなかったため、表示するよう修正した。修正後の通知は次回空庫再実行で画面確認する。

## 4. 発見して修正した問題

### 4.1 Copilot document discovery event の DB 制約不整合

症状:

```text
new row for relation "copilot_coding_task_events" violates check constraint
copilot_coding_task_events_type_valid
event_type=document_discovery_bound
```

原因:

- Repository は `document_discovery_bound` Event を記録する。
- Migration `0059` の Event Type CHECK には同 Event が含まれていなかった。

修正:

- Migration `0068_copilot_document_discovery_event.sql` を追加した。
- Migration Test で Constraint に `document_discovery_bound` が含まれることを固定した。

再確認:

- 空 Database の Migration が `0069` まで成功した。
- RAG 文書確認後、Event 記録と次工程への遷移が成功した。

結果: passed

### 4.2 Requirement RAG が無関係文書と同一文書の断片を大量表示

最初の Change ID `expense-status-all-20260802-01` では、《帳票出力》《辞書管理》などが上位を占め、同じ文書の断片が重複した。正しい《経費精算申請一覧》は候補内に存在したが、確認可能な Scope ではなかったため先へ進めなかった。

修正:

- Requirement discovery 用の候補 Pool を広げた。
- Requirement と設計書業務名の一致を優先した。
- 一文書につき最も関連する一断片だけを表示した。
- 明確な設計書業務名が Requirement に存在する場合は、その一致文書だけに Scope を限定した。

再確認:

- `expense-status-all-20260802-03` の候補は次の二文書に収束した。
  1. `02_画面設計書_経費精算申請一覧.xlsx`
  2. `03_プログラム設計書_経費精算申請.xlsx`
- 各候補から完全な Canonical 文書と実ファイル URI へ戻れる。

結果: passed

### 4.3 Launcher 以外の起動で VS Code Bridge が無効

- 開発用 `web` 単体起動では Bridge Token／Runtime Manifest が準備されず、VS Code Extension が接続できなかった。
- `operamind-launcher --root . --no-browser` の単一起動へ切り替え、Bridge 接続済みと Workspace `VisionDemo` を確認した。
- 手順上も Web／MCP の個別起動ではなく Launcher を標準入口とする。

結果: passed

### 4.4 再試行時に古い Change Task が残る

- `-01`、`-02` の旧 Task が Extension に残り、新しい `-04` より先に表示された。
- Extension から理由付きで旧 Task を取消し、現在 Task ID を照合してから再開した。
- 再試行前に旧 Task の状態を確認し、同じ業務変更の abandoned Task を取消す手順を追加対象とした。

結果: passed（運用回避）、Queue の自動整理は改善候補

### 4.5 Web 自動化が Canonical RAG 識別子を欠落

- public 表示用 Discovery を内部 Automation に保存したため、`document_snapshot_id` と `search_index_build_id` が失われた。
- Copilot の設計書差分記録が `Canonical RAG Snapshot` 不足で拒否された。
- 内部保存は完全な Canonical Discovery、Web／Copilot 表示だけ public projection を使用するよう修正した。

結果: passed（45 focused tests、Ruff、mypy）

### 4.6 確認待ちで `next_context=null` が Transaction rollback を発生

- `document_change` 保存直後に確認待ち Stage の Context を取得し、例外で DB Transaction が rollback された。
- 一方で Excel 原本は既に更新済みとなり、DB と Filesystem が不一致になった。
- MCP は確認待ちの場合、記録を commit して `next_context=null` を正常返却するよう修正した。
- 失敗試行の二 Excel Cell は基線へ戻し、再実行では二差分だけが保存された。

結果: passed（既知の外部 File と DB の完全 Atomicity は追加改善候補）

### 4.7 Code Graph 閉包が過大で再記録を反復

- 実変更候補は `ExpenseMapper.xml` 一件だったが、Code Graph 閉包は 23 Files／52 Relations となった。
- Copilot は不足 Path を一件ずつ追加して複数回 `code_scope` を再送した。
- 最終 Scope は一件だけ `modify`、残りは `review_only` として Web に表示され、利用者確認後までコード変更されなかった。

結果: passed（過大な review-only Closure と一件ずつの補正は改善候補）

### 4.8 固定 Command の並列実行と Coverage 前提不足

- Copilot が compile／test／coverage／build を同時実行し、Gradle Workspace 競合で初回 test が `launch_failed`、build が exit code 0 でも failed になった。
- 単独再実行では compile、test、build は passed した。
- Coverage は `Task 'jacocoTestReport' not found` で失敗した。対象工程には JaCoCo Plugin、XML Report 設定、`src/test` が存在しなかった。
- OperaMind は同一 Workspace の固定 Command を直列化した。
- Project の Stack 検出に Coverage Plugin、XML Report、Test Source の品質前提を追加し、不足時は `コード品質基線: blocked` として変更要件開始前に停止するよう修正した。
- Web の Project Source と初期化通知に品質基線と不足項目を表示するよう修正した。

結果: OperaMind 修正は 54 focused tests passed。VisionDemo の品質基線整備が必要なため現在 Task は正しく blocked。

### 4.9 品質情報追加時の旧 Artifact／Browser Cache 互換性

- `target_project` の新しい品質 Field を Contract の必須項目にすると、保存済みの旧 Copilot Task が validation error になった。
- 新 Field を後方互換の optional Field とし、新規 Task は Project preflight で `ready` を保証する形に修正した。旧 Artifact の Migration は不要である。
- Web HTML と JavaScript の Cache Version が同じままだと、新 JavaScript が旧 DOM に対して実行され `Cannot set properties of null` になった。
- Static asset version を更新し、Project API の public projection に `target_project` を追加した。

結果: Web で旧 Task を再読込でき、`コード品質基線: blocked` と三不足項目を表示。Coordinator の validation loop は再発していない。

## 5. 現在の工程

| 工程 | 状態 | 結果／Evidence |
|---|---|---|
| Project 初期化 | 完了 | Git `a6b055e`、Canonical 14、Vector 161 |
| 変更要件 | 完了 | `expense-status-all-20260802-04` |
| RAG 文書確認 | 完了 | 対象二文書、`document_discovery_bound` 記録成功 |
| 設計書差分 | 完了 | Excel 二 Cell、Web 差分確認済み |
| コード影響範囲 | 完了 | 23 Files／52 Relations、変更対象一件を確認済み |
| コード・テスト | blocked | XML 一件変更、compile／test／build passed、Coverage Task 不在 |
| UI 検証 | 未開始 | |
| 最終レポート | 未開始 | |

## 6. 現在の停止理由

VisionDemo の導入基線に JaCoCo Plugin、JaCoCo XML Report 設定、Test Source がない。Coverage Evidence を偽装または省略せず、対象工程の品質基線を整備して新しい空 Database 実行を開始する必要がある。現在の `-04` Task は `compile_test` で停止し、UI TestPlan／TestDataPlan／実ブラウザへ進んでいない。

## 7. 修正後の回帰結果

| 検証 | 結果 |
|---|---|
| Migration／Web／Test Case PostgreSQL integration | 21 passed |
| RAG／Migration／Web focused test | 23 passed |
| Python full regression | 716 passed、58 environment-dependent tests skipped |
| VS Code Extension | 24 passed |
| Ruff | passed |
| mypy | passed |
| Coverage readiness／Command serialization／Web 表示 focused | 54 passed |
| `git diff --check` | passed |

Live Playwright は対象 Application の起動と UiTestPlan 生成後に Section 14.11 で実施するため、この時点では未実行である。

## 8. 品質基線整備後の再実行（2026-08-03）

VisionDemo に JaCoCo と Mapper Test を追加した Commit `fa74b84` を基線として、Project
`visiondemo-manual-e2e-20260802-r3`、Change ID
`expense-status-all-20260802-r3-01` で再実行した。

- Copilot の compile、test、coverage、build はすべて passed。
- 既存実装が要件を満たすため、EditResult は検証専用 Scope の `no_changes` となった。
- Codex fallback は UI TestPlan／TestDataPlan の補正だけに使用し、Actor を
  `codex:fallback` として記録した。Copilot Evidence には置換していない。
- TestDataPlan は対象システムの HTTP API から社員一件、申請中／差戻し経費各一件を
  生成し、項目 Validation と Response Assertion を通過した。
- Playwright は三 Case の自然言語 Step、有限 Action／Assertion、Screenshot、Cleanup を
  実行し、業務カバレッジ 100%、三 Case passed となった。

実ブラウザ実行中、経費一覧 JavaScript が未定義の `formatAmount` を呼び出して一覧を
描画できない基線欠陥を検出した。VisionDemo の
`src/main/resources/static/js/app/common.js` に関数を追加し、Gradle test／build と実ブラウザ
描画を確認した。この修正は元の Copilot Task の Scope／Commit Evidence には含まれないため、
Codex fallback の独立修正 Commit `f85b465` として記録した。

同時に OperaMind の Closure で次を修正した。

1. 検証専用 Scope の `no_changes` を `in_scope` と同等に受理する。
2. Closure 作成時に対象 Workspace が EditResult の clean commit と一致することを再確認する。
3. 同一 Artifact 集合でも、評価状態または未解決項目が変わった場合は新しい immutable
   Closure Snapshot を追加できるよう、Component Digest に評価結果を含める。

修正後、古い `fa74b84` Evidence に対して `f85b465` の Workspace を使用した Closure は
`Code workspace no longer matches committed Edit Result` で正しく blocked となった。旧 Evidence
を使って成功扱いにはしていない。次回は `f85b465` を新しい基線として空 Database から実行する。

追加回帰結果:

| 検証 | 結果 |
|---|---|
| ChangeClosure／Main Flow unit | 32 passed |
| ChangeClosure PostgreSQL integration | 5 passed |
| VS Code Extension | 24 passed |
| VSIX package／install | passed |
| Ruff（Closure 関連） | passed |

## 9. 空 Database からの完全閉ループ再実行（2026-08-03 R4）

Section 8 で追加した VisionDemo の独立修正 Commit `f85b465` を新しいコード基線、
設計書 Repository の `ad23d0a7` を文書基線として、OperaMind Database を空にした状態から
次の新規 Project／Change Request で再実行した。

| 項目 | 値 |
|---|---|
| Project | `visiondemo-manual-e2e-20260803-r4` |
| Change Request | `expense-status-all-20260803-r4-01` |
| Automation Run | `web-change-automation-fdeca06c28390297c2e518fa` |
| Change Task | `web-copilot-change-task-7a2ac3a8788646ec80eb7861` |
| コード基線 | `f85b465b3522958a005cf3f0e141d3b80bf06c9b` |
| 文書基線 | `ad23d0a7` |
| AI 実行元 | `codex:fallback`（利用者が今回だけ許可） |

### 9.1 設計書、影響範囲、コード検証

- RAG から《画面設計書_経費精算申請一覧》を Canonical 文書として選択した。
- 設計書の説明一 Cell に「すべては状態で絞り込まず全件」「申請中／差戻しは既存動作を
  維持」「件数と選択状態を実ブラウザ確認」を記録し、Web で差分を確認した。
- Code Graph は UI Template、Mapper、Mapper Test、一覧描画に必要な `common.js` を影響範囲に
  含めた。
- 現在の `f85b465` が既に要件を満たすため、EditResult は検証専用 Scope の `no_changes`。
- compile、test、JaCoCo XML coverage、build はすべて passed。Closure 時にも Workspace が
  clean な同一 Commit であることを再検証した。
- 本実行は VS Code GitHub Copilot の利用可能量がないため、利用者の明示許可に基づき Codex
  fallback で実行した。Task の `claimed_by=codex-fallback-r4`、
  `accepted_by=codex:fallback` を保持し、Copilot Evidence には置換していない。

### 9.2 UI TestPlan、TestDataPlan、実ブラウザ結果

- 三つの業務要件点を三つの実行可能 UI Case に対応させ、OperaMind の自動計算で業務
  カバレッジ 100% を確認してから最終計画を確認した。
- 自然言語 TestPlan は「すべて」「申請中」「差戻し」の各検索について、画面遷移、選択、
  検索、対象データ件数、選択状態を明示した。
- TestDataPlan は被テストシステムの HTTP API で社員一件、申請中経費一件、差戻し経費一件を
  作成し、Response の ID、業務番号、状態を断言した。固定 DB ID や直接 SQL Insert は使用して
  いない。
- TestData Run `web-test-data-run-3ecb328454ed29dccf1c9e8c` は setup HTTP 3 Steps、
  Playwright UI 21 Steps、cleanup UI 1 Step、cleanup HTTP 3 Steps がすべて passed。
- 「すべて」はテスト生成対象二件、「申請中」「差戻し」は各一件を実ブラウザで確認した。
- UI Step と cleanup UI Step の Screenshot は 22 件、HTTP Request／Response Evidence は各 6 件
  保存した。
- Cleanup 後に対象 API を再検索し、`EXP-UI-R4-` 経費 0 件、`EMP-UI-R4-01` 社員 0 件を確認した。

### 9.3 最終判定と表示修正

| 判定 | 結果 |
|---|---|
| TestDataExecutionResult | `web-test-data-result-0aa6947c68cbd6baf36bd4d8` / passed |
| ChangeClosureResult | `closure-ef3e11a001a144cefbe261bc` / passed |
| UI Case | 3 / 3 passed |
| 業務カバレッジ | 100% |
| 変更行カバレッジ | 100%（検証専用 `no_changes` のため not-required 判定） |
| 未解決項目 | 0 |
| Automation | completed |

最終確認画面で、実際は Codex fallback であるにもかかわらず設計書／コード工程を固定文言の
「VS Code GitHub Copilot」と表示する問題を検出した。Task の `accepted_by` と Event Actor から
実際の AI 実行元を投影し、Codex fallback の場合は設計書差分、コード検証、AI 実行元を
`Codex fallback` と表示するよう修正した。Focused unit／frontend test は 31 passed。

Web の最終レポートを再確認し、六工程、全体進捗 100%、Closure passed、UI passed、三 Case
passed、業務カバレッジ 100% を確認して最終確認した。R4 の変更フローは完全に completed である。
