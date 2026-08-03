# OperaMind 汎用手動 E2E テスト手順

## 1. 目的

本手順は、新しく受領したコードと設計書を起点に、特定の言語、フレームワーク、ビルドツール、ブラウザー、または業務システムに依存せず、OperaMind の主変更フローを手動で E2E 確認するための共通手順である。

手動 E2E の全体順序は次のとおり。

```text
コードと設計書を受領
→ Web で Project、コード Workspace、設計書 Root を初期化
→ 原本のローカル基線を固定（Git は任意）
→ 設計書を Canonical 化して RAG Index を自動構築
→ 変更要件から技術 Profile と固定 Command を自動準備
→ Web で変更要件を登録
→ 設計書差分
→ コード影響範囲
→ コード変更・コンパイル・コードテスト・カバレッジ
→ UI TestPlan・TestDataPlan・実ブラウザ検証
→ 最終レポート
```

対象工程は次の六工程とする。

1. 変更要件
2. 設計書差分
3. コード影響範囲
4. コード変更・コンパイル・テスト
5. テストデータ・UI 検証
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

工程固有の違いは、Project Workspace、Document Root、DocumentConventionProfile、CodeFrameworkProfile、CommandExecutionProfile、TestData/UI Binding によって注入する。共通手順の中に特定工程のパス、コマンド、Locator、認証情報を固定しない。

## 3. 使用する置換値

テスト開始前に、次の値をテスト記録へ記入する。

| 置換値 | 内容 |
|---|---|
| `<OPERAMIND_ROOT>` | OperaMind vNext のルート |
| `<OPERAMIND_PACKAGE>` | 配布版を展開またはインストールした Directory |
| `<CODE_WORKSPACE>` | 対象コードを保存した設定可能なローカル Directory |
| `<DOCUMENT_ROOTS>` | 原本設計書を保存した一つ以上のローカル Directory |
| `<PROJECT_ID>` | OperaMind に登録済みの対象 Project |
| `<DATABASE_URL>` | OperaMind 用 PostgreSQL 接続 URL |
| `<TARGET_BASE_URL>` | 資格情報を含まない対象システム Origin |
| `<CHANGE_ID>` | 今回だけ使用する一意な変更番号 |
| `<REQUIREMENT>` | 業務動作と期待結果を含む自然言語要件 |

一回のテストで使用した実値は、実行前に次の表へ転記する。途中で値を変更した場合は上書きせず、変更理由と変更時刻を備考へ残す。

| 項目 | 今回の実値 |
|---|---|
| OperaMind Root | |
| Database URL（Password を除く） | |
| Code Workspace | |
| Document Root 1 | |
| Document Root 2 以降 | |
| Project ID／Project 名 | |
| UI テスト対象 URL | |
| Change ID | |
| Requirement | |
| 開始時刻 | |

`<CHANGE_ID>`、Analysis Case、Change Task は過去の実行から再利用しない。

## 4. 手動テストの原則

- Web、VS Code GitHub Copilot、対象システムの三つを利用者向け入口とする。
- 配布版では OperaMind Desktop だけを起動し、`migrate`、`web`、`mcp` を個別に実行しない。
- 設計書、Diff、Scope、TestPlan、TestDataPlan を手動で別ファイルへ移動しない。
- Copilot は OperaMind MCP が返した現在 Stage と Scope だけを扱う。
- `code_scope` が受理される前に Copilot はコードを変更しない。
- 決定的で Scope 内の遷移は OperaMind が自動承認する。
- 利用者確認は、Change Task の受領、意味判断を伴う変更、Scope 拡大、自然言語テストケース修正の最終適用に限定する。
- 受領したコードと設計書は、導入基線が完成するまで変更しない。
- コードと設計書は登録前に Git Repository でなくてもよい。Project 初期化時に OperaMind が実際の Git 帰属を判定し、未管理 Folder には内部 Git と初回 Commit を作る。
- 設計書はコード Workspace 内に固定せず、Project ごとに設定した `<DOCUMENT_ROOTS>` から取り込めるものとする。
- 既存 Git の現在 Commit、または OperaMind が作成した内部 Git の初回 Commit を、Canonical Snapshot とともに導入基線として記録する。
- Web の表示や更新操作をバックエンド処理の継続条件にしない。
- 失敗時に DB 更新、手動 commit、内部 Artifact 編集で先へ進めない。Web の停止理由と実際の Evidence を記録する。

## 5. 新しいコードと設計書から導入基線を作る

この工程は、新しい対象 Project を初めて OperaMind に登録するときに実施する。既に導入基線が確定している Project の通常変更では、登録済み基線の確認だけを行う。

### 5.1 コードと設計書をローカルに配置する

コードを `<CODE_WORKSPACE>`、設計書を一つ以上の `<DOCUMENT_ROOTS>` に保存する。両者は Git 管理外の通常 Folder でよく、同じ Directory 配下に置く必要もない。Git 管理外のコードには OperaMind が local-only の内部 Git 基線を作るが、原本を別の場所へ移動したり外部へ push したりしない。

Windows 例:

```text
C:\work\expense-system       # コード Workspace
C:\design\expense-system     # 画面・プログラム設計書
D:\shared\expense-api        # 共有 API 設計書
```

macOS／Linux 例:

```text
/Users/me/work/expense-system
/Users/me/design/expense-system
/Volumes/shared/expense-api
```

受領時点では Copilot による変更、ファイル名の一括変更、Office 文書の保存し直し、文字コード変換、自動整形を行わない。

### 5.2 Web 画面から新しい Project を初期化する

1. OperaMind Web を開く。
2. ヘッダーの「新しいプロジェクト」を押す。
3. Project ID と Project 名を入力する。
4. 「コード Workspace」に `<CODE_WORKSPACE>` の絶対 Path を入力する。
5. 「設計書の場所」に `<DOCUMENT_ROOTS>` を一行につき一つ入力する。
6. UI 影響がある場合は「UI テスト対象 URL」に `<TARGET_BASE_URL>` を入力する。後で決める場合は空欄でもよいが、UI TestPlan の確認前には設定が必要である。
7. 「初期化」を押す。

この操作では Shell Script や SQL Import を実行しない。入力した Directory は、OperaMind を実行している OS から読み取れる実在 Directory でなければならない。Windows では Windows Path、macOS／Linux では各 OS の絶対 Path を使用する。

### 5.3 初期化結果を画面で確認する

次をすべて画面から確認する。

- Project 選択欄に作成した Project が表示され、選択済みである。
- 左側の Project Source に `<CODE_WORKSPACE>` が表示される。
- 入力したすべての `<DOCUMENT_ROOTS>` が入力順に表示される。
- 既存 Git を再利用した場合は「Git」、OperaMind が作成した場合は「OperaMind 内部 Git」と表示される。
- コードと各設計書 Root に Git 基線 Commit の短縮 SHA が表示される。
- `コード品質基線` が `ready` または `blocked` として表示され、`blocked` の場合は不足している Coverage Plugin、機械可読 Report 設定、Test Source が列挙される。
- Git 管理外でも初期化が成功し、各独立 Root に `.git` と初回 Commit が作成される。
- 設計書 Root がコード Repository 内にある場合は同じ Repository Root／Commit が表示され、設計書 Root の中に Nested `.git` は作成されない。
- 既存 Repository に未 Commit 変更がある場合は自動 Commit せず、初期化が停止する。
- コード Workspace が上位 Repository の Subdirectory の場合は Nested Git を作らず、選択すべき Repository Root を表示して停止する。
- 存在しない Path、File Path、重複する Document Root は拒否される。

この時点で Git 基線と Canonical RAG 基線まで作成される。コード解析と変更要件の実行はまだ開始しない。

### 5.4 工程と文書の Profile 準備を確認する

現在の製品フローでは、利用者が Profile ファイルを手作業で登録しない。Project 初期化で DocumentConventionProfile と Embedding Binding が使われ、最初の変更要件登録時に Workspace の実ファイルから CodeFrameworkProfile と CommandExecutionProfile が自動準備される。

確認事項:

- 利用者に JSON、SQL、Shell Script の Import を要求しない。
- production、test、UI、設定、SQL、ビルド定義を含む scan root が生成される。
- 固定 Command は対象 Workspace に存在する Wrapper／Build 定義から作られる。
- Coverage Command が参照する Task と機械可読 Report が対象工程に実在し、少なくとも一つの Test Source がある場合だけ CommandExecutionProfile が ready になる。
- TestData と UI Binding は生成された Plan に明示され、未登録 Binding を推測実行しない。
- フレームワーク、文書形式、Command、UI Locator を一意に判定できない場合は、Web の現在アクションに停止理由を表示する。

停止した場合は DB や Profile ファイルを直接編集せず、表示された不足情報を Project 設定または対象 Workspace で是正してから再解析する。

### 5.5 設計書を Canonical 化する

`<DOCUMENT_ROOTS>` の各原本について次を実施する。

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

Coverage は Command 名が Profile に存在するだけでは不十分である。変更要件を登録する前に、対象工程について次を確認する。

- Coverage Plugin または Runner が Build に組み込まれている。
- Profile が要求する Report Format（JaCoCo XML、coverage.py JSON、LCOV など）が有効である。
- Report Path が Profile の `coverage_report.path` と一致する。
- 少なくとも一つの実 Test Source があり、空 Test Suite の成功を Coverage 成功として扱わない。
- 同一 Workspace の compile、test、coverage、build は直列に実行され、同じ Gradle／Build Cache を同時更新しない。

不足時は Project Source の `コード品質基線: blocked` と不足項目を記録する。Build 定義を業務変更 Task の Scope 外で暗黙変更せず、対象工程の品質基線として先に整備・固定してから変更要件を開始する。

### 5.8 導入基線の完了条件

次をすべて満たした場合だけ、Web から最初の変更要件を登録する。

- 受領コードの Revision が固定済み
- コード Workspace の初期 Snapshot が固定済み
- 設計書原本と Digest が固定済み
- Project／Workspace／基線 Revision が一意
- Canonical Snapshot が committed
- RAG Index が ready
- Section から完全な原本へ復元可能
- CodeFrameworkProfile が production／test／UI／設定を包含
- 必須 Command が登録済み
- Coverage Task、Report、Test Source の品質前提が ready
- UI Impact がある場合の対象 Origin と Binding が準備済み
- 別 Project のデータが混入しない

## 6. 一回限りの環境準備

### 6.1 対象 Project の登録確認

OperaMind に次の設定が一意に存在することを確認する。

- Project
- 対象コード Workspace
- 対象基線 Revision（Git commit またはローカル Snapshot）
- 設計文書 Root と Canonical Snapshot
- RAG Search Index と Embedding
- 自動準備された CodeFrameworkProfile
- 自動準備された CommandExecutionProfile
- 必要な TestData/UI Binding
- 対象 Deployment

CodeFrameworkProfile の scan root には、今回変更または参照する可能性がある production、test、UI、設定、ビルド定義を含める。

### 6.2 VS Code Bridge の準備

リリースの配布 ZIP を取得し、OS に応じて展開する。

```text
macOS:
<OPERAMIND_PACKAGE>/OperaMind.app
<OPERAMIND_PACKAGE>/operamind-copilot-bridge.vsix

Windows:
<OPERAMIND_PACKAGE>\OperaMind.exe
<OPERAMIND_PACKAGE>\OperaMindMcp.exe
<OPERAMIND_PACKAGE>\operamind-copilot-bridge.vsix
```

Windows では `OperaMind.exe` と `OperaMindMcp.exe` を同じ Folder に保持する。`OperaMindMcp.exe` は VS Code が内部で使用する Companion であり、利用者は起動しない。

VS Code の Extensions 画面から `Extensions: Install from VSIX...` を選び、配布 ZIP 内の `operamind-copilot-bridge.vsix` を一度だけインストールする。ソースから VSIX を作る操作は配布版の手動受入テストに含めない。

OperaMind Launcher を一度起動する。Launcher が Bridge Token と `runtime.json` をユーザー領域へ生成し、VS Code Extension が Token を SecretStorage へ同期する。Token を手作業で移動せず、Git、DB、テスト記録、チャット本文へ保存しない。

Windows のユーザー領域は次である。

```text
%LOCALAPPDATA%\OperaMind\config.env
%LOCALAPPDATA%\OperaMind\runtime.json
%LOCALAPPDATA%\OperaMind\bridge-token
```

`config.env` には Windows PostgreSQL の TCP URL を設定する。Unix socket 形式は使用しない。

```dotenv
OPERAMIND_DATABASE_URL=postgresql://operamind:<PASSWORD>@127.0.0.1:5432/operamind_vnext
```

`<TARGET_BASE_URL>` は環境変数ではなく、Web の Project 初期化画面に Project ごとに入力する。

UTF-8、UTF-8 BOM、CRLF のいずれでも読み込めることを確認する。実際の Password と Token はテスト記録へ転記しない。

### 6.3 MCP の準備

VS Code Extension がユーザー領域の `runtime.json` から OperaMind MCP を stdio 登録することを確認する。対象 Workspace に `.vscode/mcp.json` は不要である。

利用者は MCP 起動コマンドを実行しない。VS Code で MCP Server 一覧を開き、OperaMind が必要時に `Running` となることを確認する。Windows では `runtime.json` の MCP command が同じ配布 Folder の `OperaMindMcp.exe` を指し、`OperaMind.exe` を指していないことを確認する。Path と Version は記録してよいが、Bridge Token の内容は開かない。

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
- 登録済み Workspace
- MCP Tool
- GitHub Copilot Chat

## 7. 毎回の実行前確認

### 7.1 対象 Workspace

`<CODE_WORKSPACE>` を VS Code で開く。Git 管理されている場合だけ、次も確認する。

```bash
git status --short
git branch --show-current
git rev-parse HEAD
git remote get-url origin
```

開始条件は次のとおり。

- Git 管理の場合は worktree が clean で、HEAD が登録済み基線と一致する
- Git 管理外の場合は現在のファイル Digest が登録済みローカル Snapshot と一致する
- Workspace Path が Project 設定と一致する
- 前回の Change Task が active ではない

### 7.2 対象システム

対象工程固有の方法で対象システムを起動し、`<TARGET_BASE_URL>` が利用可能であることを確認する。

本共通手順では起動コマンドを固定しない。実際のコンパイル／テストコマンドは CommandExecutionProfile から Copilot へ返す。

### 7.3 OperaMind Web

配布版の主手順は次のとおり。

macOS:

1. Finder から `OperaMind.app` を起動する。
2. 既定 Browser に `http://127.0.0.1:8765/` が表示されるまで待つ。

Windows:

1. Explorer から `<OPERAMIND_PACKAGE>\OperaMind.exe` をダブルクリックする。
2. PowerShell、Command Prompt、`migrate`、`web`、`mcp` を起動しない。
3. 既定 Browser に `http://127.0.0.1:8765/` が表示されるまで待つ。
4. 起動中に不要な Console Window が残らないことを確認する。

共通の期待結果:

- Launcher が設定読込、Bridge Token 準備、DB Migration、Web 起動、Browser 表示を一回の操作で完了する。
- 単機利用の Web はユーザー名とパスワードを要求しない。
- 再度 Desktop を起動した場合は、既存の OperaMind Web を検出して Browser だけを開き、二重起動しない。
- `http://127.0.0.1:8765/health` が OperaMind の Product 情報を返す。

ソースコードから起動する開発者確認だけは、次を使用してよい。この操作を配布版の合格証跡として代用しない。

macOS／Linux:

```bash
.venv/bin/operamind-launcher --root <OPERAMIND_ROOT>
```

Windows PowerShell:

```powershell
.\.venv\Scripts\operamind-launcher.exe --root <OPERAMIND_ROOT>
```

### 7.4 Windows 固有の事前確認

Windows 配布版では次を追加確認する。

- OS が 64 bit Windows である。
- `OperaMind.exe` と `OperaMindMcp.exe` が同じ Folder にある。
- `%LOCALAPPDATA%\OperaMind\config.env` の PostgreSQL URL が TCP 接続形式である。
- `%LOCALAPPDATA%\OperaMind\runtime.json` が Desktop 起動後に生成される。
- VS Code Extension が Windows の絶対 Path を保持したまま MCP を起動できる。
- 対象 Workspace と設計書 Root に空白、日本語、Drive Letter を含む場合でも初期化できる。
- 対象工程が UI Test を必要とする場合、Microsoft Edge がインストール済みである。
- Gradle 工程では `gradlew.bat` が選択され、POSIX shell を要求しない。

## 8. 正常系の具体的操作

### Step 1: 変更要件を登録する

1. Web の `プロジェクト` から `<PROJECT_ID>` を選択する。
2. `新しい変更要件` を押す。
3. `変更番号` に `<CHANGE_ID>` を入力する。
4. `変更要件` に `<REQUIREMENT>` を入力する。
5. `変更要求を送信` を押す。

要件には少なくとも次を含める。

- 変更したい業務動作
- 入力条件
- 期待結果
- 変更しない条件
- UI 影響の有無

期待結果:

- 六工程が表示される。
- 画面上部には内部 ID ではなく変更要件の先頭文が見出しとして表示され、Change ID は小さく補助表示される。
- `現在のアクション` に、今確認または実行すべき一件だけが表示される。
- `変更要件` に人工確認が表示される。
- 現在より後の工程は `待機中` であり、過去 Task の失敗や取消状態を停止理由として表示しない。
- Web の `確認して進む`、または VS Code の `現在の工程を確認` で要件を確認する。
- 要件確認後、RAG が選んだ完全な対象設計書と根拠箇所を同じ方法で確認する。
- RAG 対象設計書の確認後だけ、VS Code Bridge が同一 Change Task を実行可能にする。

### Step 2: VS Code で Change Task を確認する

自動通知がない場合、コマンドパレットから次を実行する。

```text
OperaMind: 変更タスクを確認
```

人工確認が表示された場合は `現在の工程を確認` を押す。RAG 対象設計書まで確認した後、通知で `確認して Copilot を開く` を押す。取消または保留を選択しない。

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

差分内容が正しい場合は `確認して進む` を押す。確認前に Copilot が `code_scope` を取得できた場合は Stage 越境として失敗とする。

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
- `影響ファイルグラフ` に変更対象、依存ファイル、関連テストが表示されること
- Graph Node を選択すると Path、Language／Role、対象 Symbol、影響理由、関連テストが切り替わること
- 画面の自動更新後も選択中 Node が維持されること

この時点でもう一度 Workspace 差分を画面で確認する。Git 管理の場合は `git status --short` も使用できる。設計書以外のコード変更があれば、Copilot が Stage を飛ばしたため失敗とする。

コード範囲が正しい場合は `確認して進む` を押す。この確認後だけ Edit Packet と実行範囲が内部生成されることを確認する。

### Step 5: コードとテストの変更を確認する

Scope が受理された後、Copilot が現在 Task を再取得し、許可された production files と test files だけを変更することを確認する。

変更後に OperaMind の基線差分を確認する。Git 管理の場合は次のコマンドも使用できる。

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

### Step 6: コード Command と Coverage を確認する

working diff の受理後、必須のコンパイル・テスト・カバレッジ Command が同一 content digest に対してすべて成功し、コードを commit して `copilot_record_task_result` を記録する。OperaMind が coverage command の JaCoCo XML／coverage.py JSON／LCOV を直接解析し、変更行 80% 門禁を通過することを確認する。

Web の `コード変更・コンパイル・テスト` を開き、次を確認する。

- Copilot Task が完了
- Working Diff が Scope 内
- Compile／Code Test／Coverage Command がすべて成功
- Result Revision が変更後の Git Commit またはローカル結果 Snapshot と一致
- 変更行 Coverage が最低基準以上

### Step 7: 実 UI TestPlan / TestDataPlan を確認する

コード Command と Coverage の合格後だけ、Copilot が同じ Change Task 内で実ブラウザ用 `schema_version=v2, plan_kind=ui` の UiTestPlan と TestDataPlan を生成し、`output_stage=test_planning` を記録できることを確認する。TestPlan は単体テストの計画ではなく、実際の画面を操作する UI テスト計画である。

OperaMind は記録前に Change Request の全 Business Rule を母数として業務カバレッジを再計算する。100% 未満の場合は次を確認する。

1. Web に人工確認ボタンや UI Test 実行ボタンが表示されない。
2. Copilot Task は `test_planning` のままで、`completed` や `ui_validation` へ進まない。
3. MCP の失敗応答に `coverage_percent` と `uncovered_business_rules` が含まれる。
4. Copilot が不足 Rule を受け取り、UiTestPlan と TestDataPlan の完全版を再生成して同じ出力を再送する。
5. 単に Business Rule ID や説明文を Test Case／Evidence に追記しただけでは Coverage が上がらない。各 Business Rule は実行可能な UI Case を持ち、各 UI Case は同一 Flow の Test Data、全 `step_id` の Playwright 対応、Step ごとの Observation、Assertion、期待結果を持つ。Code Test は承認済み Test File と成功済み Command の両方を参照する。Command／Canonical／Plan Evidence は監査用の補助情報として現在 Task の実在 Source に解決できる必要があるが、それ自体で業務 Coverage を上げない。
6. 100% 到達後にだけ最終 Plan が保存され、Web の人工確認に表示される。利用者は不足項目を探したり追記したりせず、完成した最終 Plan の妥当性だけを確認する。

各 Test Case には次を含める。

- Case 名
- 前提条件
- 自然言語 Step
- 自然言語 Step と同数の一意な `step_id`
- 期待結果
- 対応する業務ルール
- 実 UI の画面遷移、操作、観測、期待結果

TestDataPlan には次を含める。

- 依存順に並んだデータ生成 Step
- 被テストシステムを実際に変更する Setup Step の構造化 `data_effect`（`creates` または `updates`）、必須 Output Binding、および同じ観測値を検証する Assertion。Action 名の「登録」「create」などの文字列だけではデータ生成と判定しない
- Step 間の変数
- 後置および最終 Assertion
- UI Step と UI Assertion
- 各 UI Step の限定 Playwright Action、Screenshot
- 各自然言語 Step を指す `test_step_refs`（未対応 Step が 0 件であること）
- Playwright で表現できない Step だけに、理由・自然言語 Objective・最大操作数・観測項目を固定した `computer_use_fallback`（通常 Step には設定しない）
- 逆順 Cleanup
- 実行不能時の blocking reason

複数画面のデータを別々の手動ファイルに分けない。一つの TestDataPlan Flow の変数、依存関係、Assertion、Cleanup として表現する。

Web の `テストデータ・UI 検証` を開き、自然言語手順、生成 Flow、変数、Assertion、Cleanup、Playwright Action と AI 画面操作への限定フォールバックを確認する。Web または VS Code で UiTestPlan／TestDataPlan を確認し、`確認して進む` を押す。確認後に実ブラウザ実行が開始され、Screenshot と最終レポートが生成されることを確認する。

### Step 8: 自然言語テストケース修正を確認する

Test Case が Web に表示された後、必要に応じて次を実行する。

1. Test Case またはデータ生成 Flow の直下にある `自然言語で修正` を押す。
2. Case 名、対象種別、現在の文言、変更後の文言を含む自然言語を入力する。対象種別にはテスト Step、期待結果、生成 Step、変数の取得元、業務 Assertion、クリーンアップ Step を指定できる。
3. `差分を確認` を押す。
4. 変更前／変更後と、曖昧な場合の選択肢を確認する。
5. `この内容を適用` を押す。これは旧計画を直接編集せず、VS Code GitHub Copilot の `ui_test_plan_revision` Task をキューに入れる操作である。

期待結果:

- Copilot が完全な新しい UiTestPlan／TestDataPlan Version を再生成し、OperaMind の検証後に保存される。
- すべての自然言語 Step が `test_step_refs` を通じて実行可能な Playwright UI Step に対応し、不足・範囲外参照・ID だけの説明は受理されない。
- 再生成が完了するまで旧 Version は current のままである。
- Coverage と下流実行が再生成される。
- 旧 Run、Evidence、Screenshot、Closure は stale 履歴になる。
- 旧 Version の結果を新 Version へ流用しない。

### Step 9: 固定コマンドと最終 Diff を確認する

Copilot が `copilot_run_task_command` で CommandExecutionProfile に登録された必須コマンドをすべて実行することを確認する。

対象工程固有のコマンド名や argv は Web/MCP が返した値を使用し、本手順では固定しない。

確認項目:

- 必須 compile／test／coverage command が欠けていない
- すべての終了状態が成功
- working diff が Scope 内
- TestPlan と TestDataPlan が記録済み
- Copilot が結果を確定（Git commit またはローカル結果 Snapshot）
- `copilot_record_task_result` が成功

実行後、Git 管理の場合は次を確認する。

```bash
git log -1 --oneline
git status --short
```

Git 管理の場合は最新 commit が今回の結果で worktree が clean であることを確認する。Git 管理外の場合は今回の結果 Snapshot が固定され、変更後 Digest と一致することを確認する。

### Step 10: TestData と UI 自動実行を確認する

Copilot の最終結果記録後、Web または VS Code に UI 実行確認が表示される。TestDataPlan の生成手順、UI Step、UI Assertion、Cleanup を確認して `確認して進む` を押す。この確認後だけ内部 Coordinator が TestData と実ブラウザ UI を自動実行する。利用者は内部 Task、Queue、Worker を操作しない。

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

Windows では、UI 実行時に Microsoft Edge が自動で使用され、利用者が Browser Driver や WSL を手動起動しないことも確認する。Edge が存在しない、対象 Origin に接続できない、または UI Binding がない場合は、代替 Browser へ黙って切り替えず blocked にする。

UI Impact がない場合は `not_impacted` または `not_required` として閉じる。

### Step 11: 最終レポートを確認する

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

最後に Web または VS Code の最終レポート確認で `確認して進む` を押す。この確認後に全体 Status が `完了` になれば正常系 E2E は合格とする。

## 9. 再開操作

VS Code または Copilot Chat を閉じた場合は、同じ `<CODE_WORKSPACE>` を再度開き、次を実行する。

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
| Revision drift | 実行中に Git HEAD またはローカル基線対象ファイルを変更 | 現在 Grant を再利用せず停止 |
| Command 失敗 | 必須 compile／test command を失敗させる | committed Result と Closure を合格にしない |
| Target URL 未設定 | Project の `test_base_url` を空にする | TestPlan の確認前に HTTP／UI 実行を fail closed |
| UI Assertion 失敗 | 期待値を意図的に不一致にする | UI／Closure が失敗し、Cleanup は実行 |
| Cleanup 失敗 | Cleanup Binding を失敗させる | Closure を合格にしない |
| Test Case 修正の曖昧性 | 対象 Case を特定できない指示を入力 | 選択肢を表示するか全体を blocked にし、部分適用しない |
| 過去 Case 再利用 | 同じ ID と古い Impact/Evidence を再利用 | 新しい結果へ流用せず拒否 |
| Windows 設定なし | `%LOCALAPPDATA%\OperaMind\config.env` を退避して `OperaMind.exe` を起動 | 設定 Path を示して停止し、空 DB や推測 URL で起動しない |
| Windows MCP Companion 欠落 | `OperaMindMcp.exe` を配布 Folder から退避して Desktop を起動 | MCP runtime の生成または診断で明示的に失敗し、`OperaMind.exe` を stdio Server として代用しない |
| Windows DB 接続失敗 | PostgreSQL を停止して `OperaMind.exe` を起動 | 日本語の起動エラーを表示し、Web を半端な状態で公開しない |
| Windows 二重起動 | Web 起動中に `OperaMind.exe` をもう一度起動 | 同じ Web を再利用し、別 Process が Port を奪わない |
| Edge 不在 | UI Impact のある Case を Edge 未導入環境で実行 | UI 検証を blocked にし、Screenshot や Closure を成功扱いにしない |

## 11. 現在重点的に確認する停止条件

次の状態が発生した場合、手動 workaround で先へ進めず、再現条件、Web 停止理由、Workspace 基線状態を記録する。

- Canonical 文書が Hash URN のみで、実ファイル参照へ復元できない
- 設計書変更後の差分状態と、Code Graph の固定基線要求が衝突する
- 新しい Change Task が過去 Analysis Case／ImpactReport と衝突する
- Copilot が `code_scope` 受理前に production code を変更する
- TestDataPlan の UI Step に Binding、Origin、唯一 Locator がない

## 12. テスト記録テンプレート

| 項目 | 記録 |
|---|---|
| 実行日時 | |
| 実行者 | |
| OS／Architecture | Windows x64 / macOS arm64 / その他 |
| OperaMind 配布 Version | |
| VSIX Version | |
| 起動入口 | Desktop / source developer mode |
| Project ID | |
| Change ID | |
| 受領元／受領日時 | |
| Design Root／原本 Digest | |
| 対象 Workspace／基線 Revision | |
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
- 配布版では利用者が `migrate`、`web`、`mcp` を個別起動していない
- Windows では Desktop、MCP Companion、VSIX が同一リリースで、Windows Runner の package smoke test が成功している

一つでも欠落する場合は合格にしない。

## 14. 空 Database から行う詳細な開発版受入手順

この章は、製品利用者の通常操作ではなく、OperaMind 自体を開発中に「過去データが一件もない状態」から主フローを検証するための実行手順である。Database の初期化だけは管理作業として Terminal を使用する。その後の Project 登録、変更要件、確認、成果物閲覧は Web と VS Code GitHub Copilot から行う。

### 14.1 今回の実行値を固定する

次の例をコピーし、実在する Path と一意な ID に置き換える。

```text
OperaMind Root:      /Users/me/work/OperaMind-vNext
Code Workspace:      /Users/me/work/target-system
Document Root:       /Users/me/work/target-documents
Project ID:          target-manual-e2e-YYYYMMDD
Project Name:        TargetSystem 手動 E2E
UI Test Target URL:  http://127.0.0.1:8080
Change ID:           change-YYYYMMDD-01
```

Requirement 例:

```text
経費精算申請一覧でステータス「すべて」を選択した場合、申請状態で絞り込まず全件を表示する。
「申請中」と「差戻し」を選択した場合の既存検索動作は変更しない。
一覧の検索結果件数と選択状態を実ブラウザで確認する。
```

同じ ID を前回データから再利用しない。Path は相対 Path、Shell の `~`、資格情報を含む URL を使用しない。

### 14.2 Database と RAG データを空にする

1. OperaMind Web、Launcher、MCP が対象 Database を使用していないことを確認して停止する。
2. `.env` またはユーザー設定の `OPERAMIND_DATABASE_URL` を確認し、開発用 Database であることを確認する。
3. PostgreSQL の `public` Schema を Drop／Create する。これにより Project、Change Request、Canonical 文書、Embedding、RAG Index、Code Graph、Task、TestPlan、Evidence がすべて削除される。
4. OperaMind の Migration を一回適用する。
5. Web を起動する。

ソース開発時の例:

```bash
psql '<DATABASE_URL>' -v ON_ERROR_STOP=1 \
  -c 'DROP SCHEMA public CASCADE; CREATE SCHEMA public;'
.venv/bin/operamind-local --root <OPERAMIND_ROOT> migrate
.venv/bin/operamind-local --root <OPERAMIND_ROOT> web
```

確認事項:

- Migration が全件成功する。
- `GET /health` が成功する。
- `GET /api/v1/projects` の `count` が `0` である。
- Project 関連 Table が 0 件である。
- Canonical／Embedding／Search Index 関連 Table が 0 件である。
- Web 左側に過去 Project や変更要件が一件も表示されない。
- Web がユーザー名と Password を要求しない。

Migration 後に参照用 Profile や Schema 管理行が存在してもよいが、過去の Project、RAG、変更、実行 Evidence が残っている場合は開始しない。

### 14.3 空画面を確認する

1. `http://127.0.0.1:8765/` を開く。
2. Header に OperaMind、Project 選択欄、`新しいプロジェクト`、`新しい変更要件` が表示されることを確認する。
3. Project 選択欄に選択肢がないことを確認する。
4. 左側に `登録済みプロジェクトがありません` が表示されることを確認する。
5. Main に `プロジェクトを初期化します` と `プロジェクトを初期化` Button が表示されることを確認する。
6. 左側だけに独立した画面全体 Scroll が発生せず、Desktop 幅では左側 Navigation が Main の縦 Scroll に追従しないことを確認する。

不合格例:

- 過去 Project が表示される。
- Request ID が一文字ずつ縦に折り返される。
- Login Dialog が表示される。
- `新しい変更要件` だけが有効で、Project を作成できない。

### 14.4 新しい Project を初期化する

1. `新しいプロジェクト` を押す。
2. Dialog の見出しが `新しいプロジェクト` であることを確認する。
3. `プロジェクト ID` に今回の Project ID を入力する。
4. `プロジェクト名` に今回の Project 名を入力する。
5. `コード Workspace` に実在する絶対 Path を入力する。
6. `設計書の場所` に一行一 Folder で実在する絶対 Path を入力する。
7. UI 影響を確認する場合は `UI テスト対象 URL` に対象 Origin を入力する。
8. 入力内容を再確認し、`初期化` を一回だけ押す。
9. Button が `RAG 基線を準備しています` に変わり、連打できないことを確認する。
10. Dialog を閉じず、処理完了まで待つ。

期待結果:

- Dialog が自動で閉じる。
- 作成した Project が選択される。
- 左側の Project Source に Workspace、UI Test URL、すべての Document Root が表示される。
- Git 管理外 Workspace は `OperaMind 内部 Git` と表示され、初回 Commit が作成される。
- 既存 Git Root は変更を追加 Commit せず、その現在 Commit を基線として表示する。
- 初期化成功通知に Canonical 設計書数と RAG Vector 数が表示され、どちらも 0 より大きい。
- Project Source に `コード品質基線` が表示され、`blocked` の場合は不足項目が初期化通知にも表示される。
- Project を再選択しても同じ情報が表示される。
- Browser を更新しても Project が Database から再読込される。

ここで失敗した場合、または `コード品質基線: blocked` の場合は変更要件を登録しない。画面に Path、Git、文書解析、Embedding、Coverage Plugin、Report、Test Source のどこで停止したかが分かる理由が必要である。

### 14.5 変更要件を登録して最初の確認を行う

1. Header の `新しい変更要件` を押す。
2. Dialog 上部に選択中 Project 名、Workspace、設計書 Folder 数が表示されることを確認する。
3. `変更番号` を今回の Change ID に変更する。
4. `変更要件` に、業務動作、入力条件、期待結果、維持条件、UI 影響を改行で分けて入力する。
5. 文字数表示が入力に追従することを確認する。
6. `変更要求を送信` を一回だけ押す。
7. 送信中に二重送信できないことを確認する。

期待結果:

- 左側の変更要件一覧に要件の先頭と Change ID が一件表示される。
- Main の大見出しは Change ID ではなく要件の先頭文である。
- Change ID は見出し下に小さく表示される。
- 全体 Status は `確認待ち` である。
- `現在のアクション` は `変更要件の確認`、担当は `利用者` である。
- 六工程は `要件`、`設計書`、`影響範囲`、`コード・テスト`、`UI 検証`、`レポート` の順である。
- 現在工程だけが開き、後続工程には未確定の古い Error や Artifact が表示されない。

要件本文と対象 Project が正しいことを確認し、`確認して進む` を押す。誤っている場合だけ `差し戻す` を押し、正しい要件を新しい Change ID で登録する。

### 14.6 RAG が選んだ設計書を確認する

1. 画面を更新せず、`現在のアクション` が次の確認へ自動遷移することを確認する。
2. `設計書差分` を開く。
3. RAG 対象設計書、根拠 Section、Source Ref を確認する。
4. Section 断片だけでなく、元の完全な設計書名と実ファイルへ戻れることを確認する。
5. Requirement と無関係な別画面、別 API、別 Project の設計書が混入していないことを確認する。
6. RAG の状態が `ready` であることを確認する。
7. 正しい場合だけ `確認して進む` を押す。

候補が 0 件、別 Project が混入、Source Ref が実ファイルへ戻れない、Embedding が未準備の場合は不合格とし、Copilot に推測させない。

### 14.7 VS Code GitHub Copilot に設計書変更を依頼する

1. `<CODE_WORKSPACE>` を VS Code で開く。
2. Workspace Trust を確認する。
3. OperaMind Extension の表示で、Bridge 接続先、Project、Change ID、現在工程を確認する。
4. `変更タスクを確認` を実行する。
5. Task の Workspace が `<CODE_WORKSPACE>`、工程が `document_change`、対象文書が Web で確認した RAG Scope と一致することを確認する。
6. `確認して Copilot を開く` を押す。
7. Copilot が OperaMind MCP の限定 Tool だけを使うことを確認する。
8. Copilot が対象設計書の完全な内容を読み、RAG Scope 内の文書だけを変更することを確認する。
9. Copilot が設計書差分を OperaMind に記録するまで待つ。

不合格条件:

- Artifact validation failed で Task 定義を取得できない。
- Workspace が別 Project を指す。
- 対象文書を手動で Chat へ貼り付ける必要がある。
- Copilot が `code_scope` の確認前に production code を編集する。

### 14.8 設計書差分とコード影響範囲を確認する

1. Web の `現在のアクション` に設計書差分確認が表示されることを確認する。
2. 変更前／変更後、対象文書、差分件数、変更理由を確認する。
3. Requirement にない文書変更がないことを確認し、`確認して進む` を押す。
4. Copilot が現在 Task を再取得し、コードを変更せず読み取り専用で影響解析することを確認する。
5. `コード影響範囲` を開く。
6. 変更対象 production file、test file、設定、UI、DB 影響を確認する。
7. `影響ファイルグラフ` で Node と Edge が表示されることを確認する。
8. Node を選び、Path、Role、Symbol、Rationale、関連 Test が切り替わることを確認する。
9. Scope が不足または過剰なら差し戻し、正しい場合だけ `確認して進む` を押す。

### 14.9 コード変更、Compile、Test、Coverage を確認する

1. Scope 確認後にだけ Copilot が production code と test code を編集することを確認する。
2. Copilot が `copilot_validate_task_diff` を実行し、Scope 外 Path が 0 件であることを確認する。
3. 固定 compile、test、coverage Command が OperaMind 経由で実行されることを確認する。
4. Command、終了 Code、Result Digest が記録されることを確認する。
5. Result Revision が固定され、再現可能であることを確認する。
6. Web の `コード変更・コンパイル・テスト` を開き、変更ファイルと Command 結果を確認する。
7. 変更行 Coverage が 80% 以上であることを確認する。

Compile、Test、Coverage の一つでも失敗した場合は UI TestPlan へ進めない。失敗中に過去成功 Evidence を表示した場合は不合格とする。

### 14.10 UI TestPlan と TestDataPlan を確認・修正する

1. Web の `テストデータ・UI 検証` を開く。
2. 各 Case に自然言語の前提条件、操作 Step、期待結果が表示されることを確認する。
3. 各自然言語 Step に対応する実行 Step が一件以上あることを確認する。
4. 複数画面を使う場合、データ生成 Flow が一つの変数系列で画面間を連結していることを確認する。
5. 入力変数、出力変数、後置 Assertion、最終 Assertion、Cleanup を確認する。
6. Playwright で実行できる操作に `computer_use_fallback` が付いていないことを確認する。
7. Canvas、画像認識など Playwright で表現できない操作だけに、理由、Objective、最大操作数、観測項目が表示されることを確認する。
8. 内容を変更する場合は `自然言語で修正` を押し、Case と変更前後を明記して入力する。
9. `差分を確認` で変更対象を確認し、正しい場合だけ `この内容を適用` を押す。
10. 新 Version が完成するまで旧 Version が current で、旧 Evidence が新 Version に流用されないことを確認する。
11. 最終的な Plan が正しい場合だけ `確認して進む` を押す。

### 14.11 TestData と実ブラウザ UI テストを確認する

1. TestData の setup が依存順に実行されることを確認する。
2. 生成値が後続画面へ変数として渡ることを確認する。
3. 対象 Browser が Project と実行環境の設定どおりであることを確認する。
4. UI 操作、画面遷移、選択状態、件数、業務 Assertion を確認する。
5. Case ごとに Screenshot が一件以上保存されることを確認する。
6. Password、Token、個人情報が Screenshot に含まれないことを確認する。
7. Cleanup が逆依存順に実行されることを確認する。
8. UI Test 失敗時も Cleanup が実行されることを確認する。
9. Web に Step ごとの成功／失敗、変数、Assertion、Screenshot、停止理由が表示されることを確認する。

### 14.12 最終レポートと再起動耐性を確認する

1. `最終レポート` を開く。
2. Requirement、設計書差分、Scope、Code Diff、Command、Coverage、UI TestPlan、TestDataPlan、Screenshot が同一 Change ID と Result Revision に結び付いていることを確認する。
3. Business Coverage が 100%、Changed-line Coverage が最低基準以上であることを確認する。
4. unresolved item と blocking reason が 0 件であることを確認する。
5. `確認して進む` を押し、全体 Status が `完了` になることを確認する。
6. Browser を更新し、完了状態と成果物が維持されることを確認する。
7. OperaMind Web を再起動し、同じ Project と Change ID を選択して完了状態が復元されることを確認する。
8. 過去の途中状態や古い Copilot Task の失敗が現在工程を上書きしないことを確認する。

### 14.13 実行終了時の記録

Section 12 の表に加えて、各工程について次を記録する。

| 工程 | 開始 | 終了 | 操作者 | 確認内容 | 結果 | Evidence／停止理由 |
|---|---|---|---|---|---|---|
| Project 初期化 | | | | Git／Canonical／RAG | | |
| 変更要件 | | | | 要件本文 | | |
| 設計書差分 | | | | RAG 文書／差分 | | |
| コード影響範囲 | | | | Graph／Scope | | |
| コード・テスト | | | | Diff／Command／Coverage | | |
| UI 検証 | | | | UI Plan／Data／Screenshot | | |
| 最終レポート | | | | Coverage／Closure | | |

失敗または停止した場合も、その時点までの合格項目と最初の停止理由を残す。DB を直接補正して同じ実行を成功扱いにせず、修正後は新しい Change ID で最初から再実行する。
