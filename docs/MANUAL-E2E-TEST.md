# OperaMind 汎用手動 E2E テスト手順

## 1. 目的

本手順は、新しく受領したコードと設計書を起点に、特定の言語、フレームワーク、ビルドツール、ブラウザー、または業務システムに依存せず、OperaMind の主変更フローを手動で E2E 確認するための共通手順である。

手動 E2E の全体順序は次のとおり。

```text
コードと設計書を受領
→ 原本と Git 基線を固定
→ Project／Repository／Profile を登録
→ 設計書を Canonical 化して RAG Index を構築
→ 対象工程の起動・テスト方法を固定
→ Web で変更要件を登録
→ 設計書差分
→ コード影響範囲
→ コード・テスト・TestPlan
→ TestData・UI 検証
→ 最終レポート
```

対象工程は次の六工程とする。

1. 変更要件
2. 設計書差分
3. コード影響範囲
4. コンパイル・テスト
5. UI 検証
6. 最終レポート

コードと設計書の受領から RAG 準備までは、六工程を開始する前の「導入基線」とする。内部の Approval、Edit Packet、Task、Queue、Lease、Worker、再試行制御は OperaMind が自動処理する。通常の手動テストでは個別に操作しない。

## 2. 適用範囲

本手順は、次のような異なる対象工程に共通して適用する。

- Java、Python、JavaScript、TypeScript、その他の対応言語
- Spring、Struts、Django、FastAPI、Node.js、その他の対応フレームワーク
- Gradle、Maven、npm、その他の固定コマンド
- Excel、Word、Markdown、PDF 由来の Canonical 設計文書
- 単画面または複数画面にまたがる業務データと UI テスト
- 任意の対応ブラウザーおよび対象 Deployment

工程固有の違いは、Project、Repository、DocumentConventionProfile、CodeFrameworkProfile、CommandExecutionProfile、TestData/UI Binding によって注入する。共通手順の中に特定工程のパス、コマンド、Locator、認証情報を固定しない。

## 3. 使用する置換値

テスト開始前に、次の値をテスト記録へ記入する。

| 置換値 | 内容 |
|---|---|
| `<OPERAMIND_ROOT>` | OperaMind vNext のルート |
| `<SOURCE_REPOSITORY_URL>` | 新しく受領したコードの Git Repository |
| `<TARGET_WORKSPACE>` | 対象コードの隔離 linked worktree |
| `<DESIGN_ROOTS>` | 新しく受領した原本設計書の設定可能な Root 群 |
| `<PROJECT_ID>` | OperaMind に登録済みの対象 Project |
| `<DATABASE_URL>` | OperaMind 用 PostgreSQL 接続 URL |
| `<TARGET_BASE_URL>` | 資格情報を含まない対象システム Origin |
| `<CHANGE_ID>` | 今回だけ使用する一意な変更番号 |
| `<REQUIREMENT>` | 業務動作と期待結果を含む自然言語要件 |

`<CHANGE_ID>`、Analysis Case、Change Task は過去の実行から再利用しない。

## 4. 手動テストの原則

- Web、VS Code GitHub Copilot、対象システムの三つを利用者向け入口とする。
- 設計書、Diff、Scope、TestPlan、TestDataPlan を手動で別ファイルへ移動しない。
- Copilot は OperaMind MCP が返した現在 Stage と Scope だけを扱う。
- `code_scope` が受理される前に Copilot はコードを変更しない。
- 決定的で Scope 内の遷移は OperaMind が自動承認する。
- 利用者確認は、Change Task の受領、意味判断を伴う変更、Scope 拡大、自然言語テストケース修正の最終適用に限定する。
- 受領したコードと設計書は、導入基線が完成するまで変更しない。
- 設計書はコード Repository 内に固定せず、Project ごとに設定した `<DESIGN_ROOTS>` から取り込めるものとする。
- Web の表示や更新操作をバックエンド処理の継続条件にしない。
- 失敗時に DB 更新、手動 commit、内部 Artifact 編集で先へ進めない。Web の停止理由と実際の Evidence を記録する。

## 5. 新しいコードと設計書から導入基線を作る

この工程は、新しい対象 Project を初めて OperaMind に登録するときに実施する。既に導入基線が確定している Project の通常変更では、登録済み基線の確認だけを行う。

### 5.1 受領物を分離して保存する

コードは `<SOURCE_REPOSITORY_URL>` から clone し、受領時点の Revision を変更せずに保存する。

```bash
git clone <SOURCE_REPOSITORY_URL> <TARGET_WORKSPACE>
cd <TARGET_WORKSPACE>
git status --short
git branch --show-current
git rev-parse HEAD
git remote get-url origin
```

設計書は設定可能な `<DESIGN_ROOTS>` に保存する。コード Repository と同じ Directory である必要はない。

例:

```text
<TARGET_WORKSPACE>      # コードの Git worktree
<DESIGN_ROOTS>/screen  # 画面設計書
<DESIGN_ROOTS>/program # プログラム設計書
<DESIGN_ROOTS>/api     # API 設計書
<DESIGN_ROOTS>/db      # DB 設計書
```

受領時点では次を禁止する。

- Copilot による変更
- ファイル名の一括変更
- Excel／Word の保存し直し
- 文字コードや改行の自動変換
- 生成物の削除を含む自動整形

初期配置は導入のための一回限りの取込であり、Change Task の工程間ファイル handoff ではない。

### 5.2 受領物の Inventory を記録する

最低限、次を記録する。

- Repository URL
- Branch
- commit SHA
- code root
- design root
- 設計書の論理名、種類、相対 Path
- 各原本の SHA-256
- 受領日時と受領元

Git 管理下のコードについて確認する。

```bash
git status --short
git ls-files
git rev-parse HEAD
```

設計書については、対象 OS で利用できる SHA-256 Tool を使い、原本の Digest を記録する。秘密情報、DB Password、API Key、Bridge Token は Inventory に含めない。

### 5.3 Project と Repository を登録する

OperaMind に新しい `<PROJECT_ID>` を作成し、次を一意に登録する。

- Project 名
- Repository ID
- Repository remote
- `<TARGET_WORKSPACE>` の絶対 Path
- 受領時点の commit SHA
- Repository Revision
- 新しい Analysis Case を作成するための Project 関係

同じ Project に複数の曖昧な Workspace または Repository 登録を残さない。

登録後に、OperaMind の Repository 情報と実際の Git 情報が一致することを再確認する。

### 5.4 工程と文書の Profile を決める

対象コードと設計書を読み取り、次を明示的に選択または作成する。

- DocumentConventionProfile
- CodeFrameworkProfile
- CommandExecutionProfile
- 必要な TestData Binding
- 必要な UI Action／Assertion Binding

CodeFrameworkProfile では、対象工程の production、test、UI、設定、SQL、ビルド定義を取りこぼさない scan root と language を設定する。

フレームワークや文書形式を一意に判定できない場合は、推測で続行せず導入基線を blocked にする。

### 5.5 設計書を Canonical 化する

`<DESIGN_ROOTS>` の各原本について次を実施する。

1. 論理 Document ID を割り当てる。
2. DocumentConventionProfile を適用する。
3. Sheet／Section／表／項目を抽出する。
4. Stable Key と Source Ref を生成する。
5. 原本の実ファイル URI と content digest を保存する。
6. Canonical Snapshot を committed にする。
7. Document Node を生成する。

確認項目:

- Section 断片から `document_id` を取得できる。
- `document_id` から完全な設計書と実ファイルへ戻れる。
- Source Ref が存在する原本へ解決できる。
- 同一 Snapshot 内で Stable Key が重複しない。
- 原本 Digest と Canonical Document Version が一致する。

### 5.6 RAG Index を構築して検索確認する

Project の Canonical Snapshot を対象に Embedding と Search Index を構築する。

最低限、次の検索を手動確認する。

1. 画面変更の自然言語から画面設計書を取得
2. API 変更の自然言語から API／プログラム設計書を取得
3. DB 変更の自然言語から DB／プログラム設計書を取得
4. 別 Project の文書が混入しないことを確認
5. Section 候補から完全な原本へ戻れることを確認

Index 未準備、Embedding 失敗、Project 越境、原本参照欠落がある場合は、変更要件を開始しない。

### 5.7 コード基線と固定コマンドを確認する

変更を加えていない受領コードに対して、Project 固有の固定コマンドを実行し、開始時点の状態を記録する。

確認対象:

- compile
- unit test
- integration／API test
- 必要な static check
- coverage
- 対象システム起動

コマンド本文は対象 Project の CommandExecutionProfile に登録する。共通手順へ特定の Gradle、Maven、npm、pytest などを固定しない。

受領時点ですでに失敗するコマンドがある場合は、既知基線失敗として明示し、今回の変更による失敗と混同しない。ただし必須 Command が実行不能な Project を ready にしない。

### 5.8 導入基線の完了条件

次をすべて満たした場合だけ、Web から最初の変更要件を登録する。

- 受領コードの Revision が固定済み
- worktree が clean
- 設計書原本と Digest が固定済み
- Project／Repository／Revision が一意
- Canonical Snapshot が committed
- RAG Index が ready
- Section から完全な原本へ復元可能
- CodeFrameworkProfile が production／test／UI／設定を包含
- 必須 Command が登録済み
- UI Impact がある場合の対象 Origin と Binding が準備済み
- 別 Project のデータが混入しない

## 6. 一回限りの環境準備

### 6.1 対象 Project の登録確認

OperaMind に次の設定が一意に存在することを確認する。

- Project
- Repository と対象 Workspace
- 対象 Git Revision
- 設計文書 Root と Canonical Snapshot
- RAG Search Index と Embedding
- CodeFrameworkProfile
- CommandExecutionProfile
- 必要な TestData/UI Binding
- 対象 Deployment

CodeFrameworkProfile の scan root には、今回変更または参照する可能性がある production、test、UI、設定、ビルド定義を含める。

### 6.2 VS Code Bridge の準備

`<OPERAMIND_ROOT>` で VSIX を作成する。

```bash
cd <OPERAMIND_ROOT>/vscode-extension
npm ci
npm run package:vsix
```

VS Code の `Extensions: Install from VSIX...` から、次をインストールする。

```text
<OPERAMIND_ROOT>/vscode-extension/dist/operamind-copilot-bridge.vsix
```

Bridge Token を新規生成する。

```bash
python3 -c 'import secrets; print(secrets.token_urlsafe(32))'
```

同じ Token を次の二箇所だけに設定する。

1. OperaMind Web の `OPERAMIND_BRIDGE_TOKEN`
2. VS Code の `OperaMind: Bridge Token を安全に登録`

Token を Git、DB、テスト記録、チャット本文へ保存しない。

### 6.3 MCP の準備

対象 Workspace から OperaMind MCP を stdio 起動できるようにする。MCP Server は `<OPERAMIND_ROOT>` の Contract と `<DATABASE_URL>` を使用する。

Copilot から見える Tool が次の五つだけであることを確認する。

- `copilot_get_coding_task`
- `copilot_record_change_outputs`
- `copilot_validate_task_diff`
- `copilot_run_task_command`
- `copilot_record_task_result`

### 6.4 ローカル診断

対象 Workspace を VS Code で開き、Workspace Trust を有効にする。

コマンドパレットから次を実行する。

```text
OperaMind: ローカル環境を診断
```

次の項目が成功するまで本テストを開始しない。

- VSIX
- Loopback Bridge
- Bridge Token
- Workspace Trust
- linked worktree
- MCP Tool
- GitHub Copilot Chat

## 7. 毎回の実行前確認

### 7.1 対象 Workspace

`<TARGET_WORKSPACE>` で実行する。

```bash
git status --short
git branch --show-current
git rev-parse HEAD
git remote get-url origin
```

開始条件は次のとおり。

- worktree が clean
- 現在 Branch が今回の隔離 Branch
- HEAD と Repository Revision が一致
- remote と Repository 登録が一致
- 前回の Change Task が active ではない

### 7.2 対象システム

対象工程固有の方法で対象システムを起動し、`<TARGET_BASE_URL>` が利用可能であることを確認する。

本共通手順では起動コマンドを固定しない。実際のコンパイル／テストコマンドは CommandExecutionProfile から Copilot へ返す。

### 7.3 OperaMind Web

```bash
cd <OPERAMIND_ROOT>
export OPERAMIND_DATABASE_URL='<DATABASE_URL>'
export OPERAMIND_BRIDGE_TOKEN='<Bridge Token>'
export OPERAMIND_TEST_TARGET_BASE_URL='<TARGET_BASE_URL>'

.venv/bin/operamind-migrate
.venv/bin/operamind-web --root <OPERAMIND_ROOT> --host 127.0.0.1 --port 8765
```

ブラウザーで `http://127.0.0.1:8765/` を開く。単機利用の Web にはユーザー名とパスワードを要求しない。

## 8. 正常系の具体的操作

### Step 1: 変更要件を登録する

1. Web の `プロジェクト` から `<PROJECT_ID>` を選択する。
2. `新しい変更要件` を押す。
3. `変更番号` に `<CHANGE_ID>` を入力する。
4. `変更要件` に `<REQUIREMENT>` を入力する。
5. `登録して開始` を押す。

要件には少なくとも次を含める。

- 変更したい業務動作
- 入力条件
- 期待結果
- 変更しない条件
- UI 影響の有無

期待結果:

- 六工程が表示される。
- `変更要件` が完了する。
- `設計書差分` が進行中または待機中になる。
- VS Code Bridge が同一 Change Task を検出する。

### Step 2: VS Code で Change Task を確認する

自動通知がない場合、コマンドパレットから次を実行する。

```text
OperaMind: 変更タスクを確認
```

通知で `確認して Copilot を開く` を押す。取消または保留を選択しない。

Copilot Chat に Prompt が未送信で残っている場合だけ `Enter` を押す。MCP の限定 Tool 実行確認は本セッションに対して許可する。任意 Shell、範囲外 Directory、追加 Credential は許可しない。

### Step 3: 設計書差分を確認する

Copilot が次を行うことを確認する。

1. `copilot_get_coding_task` で `document_change` を取得
2. RAG Section 候補を取得
3. `document_id` から完全な Canonical 文書と実ファイルを復元
4. 候補内の設計書だけを変更
5. `copilot_record_change_outputs` を `output_stage=document_change` で実行

`document_change` では `document_ids` だけを送り、空の `code_scope`、`test_plan`、`test_data_plan` を送らない。

Web の `設計書差分` で次を確認する。

- 対象設計書
- 変更前／変更後
- 差分件数
- 業務要件との整合
- 停止理由がないこと

この時点で RAG Scope 外の設計書が変更されていた場合は失敗とする。

### Step 4: コード影響範囲を確認する

Copilot が次を行うことを確認する。

1. 現在 Task を再取得
2. 変更済み設計書を根拠にコードを読み取り専用で調査
3. Path、Symbol、Action、Test Binding、Rationale、UI Impact を提示
4. `copilot_record_change_outputs` を `output_stage=code_scope` で実行

Web の `コード影響範囲` で次を確認する。

- 変更対象コード
- テストファイル
- UI 影響
- 影響解析状態
- Scope 外候補がないこと

この時点でもう一度 `git status --short` を実行する。設計書以外のコード変更があれば、Copilot が Stage を飛ばしたため失敗とする。

### Step 5: コードとテストの変更を確認する

Scope が受理された後、Copilot が現在 Task を再取得し、許可された production files と test files だけを変更することを確認する。

変更後に次を実行する。

```bash
git status --short
git diff --stat
git diff
```

確認項目:

- 変更 Path が Scope 内
- 必要な正常系、境界値、異常系テストが追加済み
- 要件外リファクタリングがない
- キャッシュ、ログ、生成物、Credential がない
- 新しい依存関係または Command が Scope と Profile の範囲内

Copilot が `copilot_validate_task_diff` を実行し、working diff が `in_scope` になることを確認する。

### Step 6: TestPlan / TestDataPlan を確認する

working diff の受理後、Copilot が同じ Change Task 内で TestPlan と TestDataPlan を生成し、`output_stage=test_planning` を記録することを確認する。

各 Test Case には次を含める。

- Case 名
- 前提条件
- 自然言語 Step
- 期待結果
- 対応する業務ルール
- API／UI などの検証 Channel

TestDataPlan には次を含める。

- 依存順に並んだデータ生成 Step
- Step 間の変数
- 後置および最終 Assertion
- UI Step と UI Assertion
- 逆順 Cleanup
- 実行不能時の blocking reason

複数画面のデータを別々の手動ファイルに分けない。一つの TestDataPlan Flow の変数、依存関係、Assertion、Cleanup として表現する。

### Step 7: 自然言語テストケース修正を確認する

Test Case が Web に表示された後、必要に応じて次を実行する。

1. `修正を提案` を押す。
2. Case 名、現在の文言、変更後の文言を含む自然言語を入力する。
3. `差分を確認` を押す。
4. 変更前／変更後と、曖昧な場合の選択肢を確認する。
5. `この内容を適用` を押す。

期待結果:

- 新しい TestPlan／TestDataPlan Version が生成される。
- Coverage と下流実行が再生成される。
- 旧 Run、Evidence、Screenshot、Closure は stale 履歴になる。
- 旧 Version の結果を新 Version へ流用しない。

### Step 8: 固定コマンドと最終 Diff を確認する

Copilot が `copilot_run_task_command` で CommandExecutionProfile に登録された必須コマンドをすべて実行することを確認する。

対象工程固有のコマンド名や argv は Web/MCP が返した値を使用し、本手順では固定しない。

確認項目:

- 必須 compile／test／coverage command が欠けていない
- すべての終了状態が成功
- working diff が Scope 内
- TestPlan と TestDataPlan が記録済み
- Copilot が結果を commit
- `copilot_record_task_result` が成功

実行後に確認する。

```bash
git log -1 --oneline
git status --short
```

最新 commit が今回の結果で、worktree が clean であることを確認する。

### Step 9: TestData と UI 自動実行を確認する

Copilot の最終結果記録後は、内部 Coordinator が自動で後続処理を行う。利用者は内部 Task、Queue、Worker を操作しない。

Web の `UI 検証` で次を確認する。

- TestDataPlan の generation flow
- Fixture／HTTP／SQL／UI Step の実行状態
- 入力変数と出力変数
- 後置／最終 Assertion
- Cleanup
- UI 操作結果
- Screenshot Evidence
- blocking reason

UI Impact がある場合の合格条件:

- すべての setup Step が成功
- UI Step が成功
- `observe_via=ui` の Assertion が成功
- サニタイズ済み Screenshot が一件以上
- Cleanup が成功

UI Impact がない場合は `not_impacted` または `not_required` として閉じる。

### Step 10: 最終レポートを確認する

Web の `最終レポート` で次を確認する。

- Closure status が `passed`
- Business coverage が 100%
- Changed-line coverage が Task の最低基準以上
- Modified paths が Scope 内
- 必須 Command がすべて成功
- TestData と Cleanup が成功
- UI Impact がある場合は UI Result と Screenshot が成功
- unresolved item がない
- Requirement、Document Diff、Impact、Code Diff、TestPlan、TestDataPlan、Command Evidence、UI Evidence が同一 Project／Case／Revision に結び付く

全体 Status が `完了` になれば正常系 E2E は合格とする。

## 9. 再開操作

VS Code または Copilot Chat を閉じた場合は、同じ `<TARGET_WORKSPACE>` を再度開き、次を実行する。

```text
OperaMind: 現在のタスクを再開
```

新しい Change Request を作らず、同じ Task ID と現在 Stage から再開する。Terminal 状態の Task は再開しない。

## 10. 必須の異常系確認

| ケース | 操作 | 期待結果 |
|---|---|---|
| RAG 未準備 | Index または Canonical 文書が利用できない Project で開始 | `設計書差分` で停止し、Copilot がファイルを推測しない |
| 文書 Scope 越境 | RAG 候補外の文書を変更 | `document_change` が失敗 |
| 早期コード変更 | `code_scope` 受理前にコードを変更 | Scope 工程を合格にしない |
| Code Scope 越境 | 許可されていない Path／Symbol／Test を提示 | Graph 検証で停止 |
| Diff 越境 | Scope 外ファイルを変更 | `reanalysis_required` |
| Revision drift | 実行中に HEAD を変更 | 現在 Grant を再利用せず停止 |
| Command 失敗 | 必須 compile／test command を失敗させる | committed Result と Closure を合格にしない |
| Target URL 未設定 | `OPERAMIND_TEST_TARGET_BASE_URL` を設定しない | 外部 HTTP／UI を fail closed |
| UI Assertion 失敗 | 期待値を意図的に不一致にする | UI／Closure が失敗し、Cleanup は実行 |
| Cleanup 失敗 | Cleanup Binding を失敗させる | Closure を合格にしない |
| Test Case 修正の曖昧性 | 対象 Case を特定できない指示を入力 | 選択肢を表示するか全体を blocked にし、部分適用しない |
| 過去 Case 再利用 | 同じ ID と古い Impact/Evidence を再利用 | 新しい結果へ流用せず拒否 |

## 11. 現在重点的に確認する停止条件

次の状態が発生した場合、手動 workaround で先へ進めず、再現条件、Web 停止理由、Git 状態を記録する。

- Canonical 文書が Hash URN のみで、実ファイル参照へ復元できない
- 設計書変更後の dirty 状態と、Code Graph の clean Revision 要求が衝突する
- 新しい Change Task が過去 Analysis Case／ImpactReport と衝突する
- Copilot が `code_scope` 受理前に production code を変更する
- TestDataPlan の UI Step に Binding、Origin、唯一 Locator がない

## 12. テスト記録テンプレート

| 項目 | 記録 |
|---|---|
| 実行日時 | |
| 実行者 | |
| Project ID | |
| Change ID | |
| 受領元／受領日時 | |
| Design Root／原本 Digest | |
| 対象 Repository／Branch | |
| 開始 Revision | |
| 結果 Revision | |
| 対象 Deployment | |
| Requirement | |
| Document Change | passed / failed |
| Code Scope | passed / failed |
| Working Diff | passed / failed |
| TestPlan／TestDataPlan | passed / failed |
| 必須 Command | passed / failed |
| TestData／Cleanup | passed / failed |
| UI／Screenshot | passed / failed / not impacted |
| Business Coverage | |
| Changed-line Coverage | |
| Closure | passed / failed / blocked / reanalysis required |
| Blocking Reason | |
| Evidence Ref | |
| 備考 | |

## 13. 合格判定

正常系は次をすべて満たした場合だけ合格とする。

- 受領コードと設計書の導入基線が固定済み
- Canonical Snapshot と RAG Index が ready
- 六工程が順番どおりに完了
- RAG から実設計書へ復元できる
- 設計差分が Canonical 化される
- Code Scope が Graph と Test Binding で検証される
- Copilot の変更が Scope 内
- 必須 Command がすべて成功
- TestPlan／TestDataPlan が変更要件と Diff に整合
- TestData、Assertion、Cleanup が成功
- UI Impact がある場合は UI Step、Assertion、Screenshot が成功
- Business coverage が 100%
- Changed-line coverage が最低基準以上
- 同一 Project／Case／Revision に Evidence が結合
- ChangeClosureResult が `passed`

一つでも欠落する場合は合格にしない。
