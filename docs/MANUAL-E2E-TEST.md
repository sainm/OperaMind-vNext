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

この時点では Git 基線と Project 設定が保存され、Onboarding がバックグラウンドで開始される。左側の表示が `構造抽出 → 設計書学習` へ進み、「設計書学習」から Sample 数と現在状態を確認できること。VS Code 上の GitHub Copilot が草案を返した後、Field Mapping、Stable Key、曖昧点、Coverage を確認する。Coverage 100%／曖昧 0 件だけ「確認して適用」が有効になり、確認後は `Canonical 文書 → RAG 索引 → 準備完了` を自動継続する。Browser を閉じても処理条件は失われない。

### 5.4 工程と文書の Profile 準備を確認する

現在の製品フローでは、利用者が Profile ファイルを手作業で登録しない。Project 初期化で実設計書の構造を抽出し、VS Code 上の GitHub Copilot が Project 専用 DocumentConventionProfile 草案を作る。確認済み Version は PostgreSQL に保存され、Canonical Data と RAG Index を更新する。最初の変更要件登録時には Workspace の実ファイルから CodeFrameworkProfile と CommandExecutionProfile が自動準備される。

確認事項:

- 利用者に JSON、SQL、Shell Script の Import を要求しない。
- 別 Project の DocumentConventionProfile が候補に混入しない。
- 設計書の業務値だけを変更した再スキャンは現行 Profile を再利用し、Sheet、Heading、Header を変更した再スキャンは再学習で停止する。
- production、test、UI、設定、SQL、ビルド定義を含む scan root が生成される。

被テストシステムの DB へ直接データを準備する Project では、Project 設定の「被テストシステム DB データ準備」を開く。通常の変更利用者が SQL を毎回入力するのではなく、管理者／QA が事前レビューした Binding 定義を一度登録する。Database 方言が登録済み Adapter（現在は `postgresql`）であることを確認し、接続 Alias と PostgreSQL 接続 Secret を入力する。write Binding ごとに `query_binding_id`、命名 Parameter、入力制約、対象 Table／Column、read-after-write、cleanup Binding、Transaction、冪等方針が揃うことを確認する。Secret は保存後に画面へ再表示されず、空欄で再保存すると現在値を維持する。未登録方言は Profile／Secret 保存または Plan 確認で blocked となり、PostgreSQL へ fallback しないことを確認する。

確認点：

- Project 一覧には Alias、Binding 件数、Secret 設定有無だけが表示され、Password や SQL 本文は表示されない。
- VS Code 上の GitHub Copilot の `target_data_bindings` には Binding ID と制約だけがあり、接続 URL、Password、SQL 本文がない。
- Copilot が `target` に任意 SQL を出力した Plan は未登録 Binding として確認前に失敗する。
- write Step に対応する cleanup Step がない、入力値が型／長さ／必須／列挙・業務制約に反する、実 DB の Table／Column が変わった、回読結果が不一致の場合は実 UI Test へ進まない。
- 同じ Run／同じ業務 Key の再実行は Binding の冪等方針に従い、cleanup は失敗した setup の後でも既存の実行 Engine 規則に従って試行される。
- Fixture Channel は製品画面から設定できず、自動テストの明示注入だけに限定される。
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

コード Command と Coverage の合格後だけ、Copilot が同じ Change Task 内で実ブラウザ用 `schema_version=v2, plan_kind=ui` の UiTestPlan と、RunContext／Frozen Binding を持つ `schema_version=v3` の TestDataPlan を生成し、`output_stage=test_planning` を記録できることを確認する。TestPlan は単体テストの計画ではなく、実際の画面を操作する UI テスト計画である。既存の TestDataPlan v1／v2 は履歴表示だけに使用し、新しい正式 Plan または Locator 修正版として確認・実行しない。

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
- 各 `test_data_id` を登録済みの実 `DataIdentityProvider`（database／api／ui／hybrid）へ結ぶ `identity_binding`。Provider は `primary_key`、`business_unique_keys`、`screen_identity_values`、`record_scope_locator`、`match_count`、`evidence_ref` を返し、`match_count` は必ず 1 であること。Hybrid は全 Source を同名・同値 Identity で連結し、同一業務レコードであることを検証すること。未登録 Provider、fake、推測、静かな fallback は使用しないこと
- 確定した全 AcceptanceCriteria／TestCase／`test_data_id` の組合せに対応する `coverage_conditions`。業務項目、状態、境界値、関連関係を、Identity Binding と同じ確認済み Provider Observation の項目で検証すること。database Provider の場合はレビュー済み SQL readback の列を使用する
- 全 UI Step の `operation_scope=screen|bound_record`。跨画面および表 UI Step は `bound_record` と `data_binding_ref` を持ち、画面識別キーから生成した exact Locator の Scope 内だけを操作し、行番号・曖昧 Text・AI 推測を使用しないこと
- Copilot が画面設計書、HTML／Template／Frontend Source、Route、Code Graph、Test Case、TestDataPlan、DataIdentityProvider を根拠に、画面ごとに role + name、label、placeholder、test id、title、alt text、安全な CSS、または全 `screen_identity_values` を含む複合 Locator を選択していること。固定 ID や固定表構造を全画面へ強制しないこと
- click、fill、select など状態を変える Action に、実行直前に読む期待値付き `pre_action_observations` があること。`nth()`、`nth-child`、行番号、曖昧 Text、座標、未検証 Dynamic CSS がないこと
- 後置および最終 Assertion
- UI Step と UI Assertion
- 各 UI Step の限定 Playwright Action、Screenshot
- 各自然言語 Step を指す `test_step_refs`（未対応 Step が 0 件であること）
- Playwright で表現できない未 Binding Step だけに、理由・自然言語 Objective・最大操作数・観測項目を固定した `computer_use_fallback`（通常 Step および `data_binding_ref` を持つ Step には設定しない）
- 逆順 Cleanup
- 実行不能時の blocking reason

既存の実データを使用する Test Case では、Plan の最終確認前に次の操作を行う。

1. Project の「既存テストデータ」を開き、「既存テストデータを登録」を表示する。
2. データ名、業務番号または業務上の一意値、使用する Test Case、テスト終了後の保持有無だけを入力する。SQL、主キー項目名、JSON、Locator、`stable_key`、内部 Binding ID の入力欄がないことを確認する。
3. 「一意性を確認」を押し、確認済み database／api／ui／hybrid Provider が実行された結果を待つ。
4. 0 件、複数件、Provider 未設定、Evidence 不足では `blocked` となり、確認ボタンと adopted データ定義が生成されないことを確認する。
5. 1 件の場合は脱敏された業務摘要、Provider 種別、保持方針を確認し、「確認して採用」を押す。
6. 人工確認後だけ「固定データ識別子」の実行前計画へ `adopted` データとして現れることを確認する。既存業務値が暗黙に書き換えられていないことを確認する。

複数画面のデータを別々の手動ファイルに分けない。一つの TestDataPlan Flow の変数、依存関係、Assertion、Cleanup として表現する。

Web の `テストデータ・UI 検証` を開き、自然言語手順、生成 Flow、変数、Assertion、Cleanup、Playwright Action と AI 画面操作への限定フォールバックを確認する。Web または VS Code で UiTestPlan／TestDataPlan を確認し、`確認して進む` を押す。確認後に実ブラウザ実行が開始され、Screenshot と最終レポートが生成されることを確認する。

実行後は同画面の `固定データ識別子` で、業務値、Run Token、Provider 種別、使用した Test Case／Flow／Step、Evidence、Cleanup 結果を確認する。普通利用者画面に Provider Ref、主キー、Locator、digest、SQL、Secret、内部 Binding ID が表示されないことも確認する。管理者は内部 Artifact／診断で `match_count=1`、identity digest、Evidence の Scope 一致を監査できる。続けて `実データ条件の検証結果` で AcceptanceCriteria、TestCase、TestData、Observation Source、条件、期待値、実測値、Evidence を確認する。Test Data Coverage は OperaMind がこれらの実測 Proof から算出し、100% 未満では TestPlan の UI Step、Screenshot、最終成功へ進まない。対象 Provider では 1 件でも、画面上で 0 件または複数件になった場合も UI Step が `blocked` となる。Secret、接続情報、認証値が DB、ログ、Copilot Context、Evidence にないことも確認する。

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

### Step 9: 最終 Diff と固定 Command Evidence を再確認する

Step 6 で Copilot が `copilot_run_task_command` を使って実行・確定した CommandExecutionProfile の Evidence を再確認する。UI TestPlan の生成または自然言語 Revision はコードを変更しないため、同じ Result Revision／content digest に対する成功 Evidence を再利用し、同じ Command を理由なく二重実行しない。

Plan Revision 中に production code、test code、build file の差分が増えた場合は Plan だけの変更として受理しない。Code Scope と Result Revision が変わるため `コード影響範囲` へ戻り、新しい Scope 確認、compile、test、coverage を完了してから新しい TestPlan を生成する。

対象工程固有のコマンド名や argv は Web/MCP が返した値を使用し、本手順では固定しない。

確認項目:

- 必須 compile／test／coverage command が欠けていない
- すべての終了状態が成功
- working diff が Scope 内
- TestPlan と TestDataPlan が記録済み
- Copilot が Step 6 で結果を確定済み（Git commit またはローカル結果 Snapshot）
- Step 6 の `copilot_record_task_result` が成功し、現在の Plan が同じ Result Revision を参照

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

正式 Run の前に別の Browser 予行 Run が作られていないことを確認する。一つの Browser Context で画面へ移動し、各状態変更 Action の直前に Origin、画面状態、対象 Locator の件数を読み取る。`bound_record` では現在 Project／Run の frozen Binding、record Scope count=1、全業務 Identity 値、全 `screen_identity_values`、`observed_identity_digest`、Scope 内 Action count=1 を確認してから操作する。

Locator を意図的に一つ drift させる確認では、次を合格条件とする。

1. Scope または Action が 0 件／複数件、Identity 不一致、Origin 越境のいずれかになった時点でクリックや更新を実行しない。
2. Run が `blocked` になり、脱敏 Screenshot、Step Log、Locator 種別、Scope／Action match count、失敗工程が Evidence に残る。
3. Web／Chat の通常表示には業務目的、対象データ、画面、阻断理由、次の操作だけが表示され、Locator JSON、digest、Raw DOM、内部 Artifact ID、MCP Raw I/O がない。
4. 利用者が転記操作をしなくても、Evidence が同じ Copilot Change Task に記録され、OperaMind が `ui_test_plan_revision` Task を自動発行し、Copilot が業務期待値を変えずに完全な新 Plan を生成する。発行不能時は `locator_revision_publish_failed` が表示され、元の blocked Evidence は保持される。
5. 新 Plan は同じ Confirmation API で再確認が必要となり、元 Run の途中では Locator が差し替えられない。
6. `bound_record` または単なる Locator 誤りに Computer Use fallback が起動しない。許可済み Canvas／Native Dialog などの fallback 後も Playwright が Observation と Screenshot を再取得する。

UI Impact がある場合の合格条件:

- すべての setup Step が成功
- UI Step が成功
- `observe_via=ui` の Assertion が成功
- サニタイズ済み Screenshot が一件以上
- Cleanup が成功
- Binding 付き UI Cleanup の操作後に同じ Frozen Scope が 0 件となり、Database／API Source がある場合は対応 Provider でも対象レコード不存在が確認される
- 各 UI Step Result に driver、Locator 種別、Scope／Action match count、実画面 Identity、Binding ref、Assertion、Step Log、Screenshot が結び付く

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
- 各業務要件から Test Case、UI Step、凍結データ、Provider、実レコード、Assertion／Screenshot、Cleanup までの追跡があり、全 Binding ref が現在の Project／Run に解決する

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
- DB readback または画面 Scope が 0 件／複数件、実 DOM の業務一意キー／画面キーが欠落・不一致、あるいは固定 Binding の digest が drift した。bound record の Action は実行されず、Step Log の `observed_identity_digest` は DOM 実値から計算され、`content_digest` と異なること

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

#### 14.1.1 完全閉ループ用の業務シナリオを一つ固定する

単に Button を一回押せるだけの要件では、データ生成、跨画面 Binding、Cleanup、Evidence の全経路を確認できない。完全閉ループ受入では、対象業務に合わせて次の性質を持つシナリオを一つ選ぶ。対象システムに該当機能がない場合は、存在しない機能を作らず、該当しない項目を `not_required` とした理由を記録する。

| 固定項目 | 今回の値 | 合格条件 |
|---|---|---|
| 業務一意キー | 例: 経費番号 | DB／API／画面の同じレコードへ解決する |
| 初期状態 | 例: `RETURNED` | Setup または既存データ検索で実測できる |
| 画面 A | 例: 一覧画面 | 一意キーを含む Record Scope が一件になる |
| 画面 B | 例: 詳細画面 | 画面 A と同じ Binding を参照する |
| 状態変更 Action | 例: 再申請 | Action 前後の業務状態を観測できる |
| 最終状態 | 例: `APPLIED` | UI と、利用可能な DB／API Provider で一致する |
| Cleanup | 例: 作成データ削除 | 元の Frozen Binding を使用し、削除後 0 件になる |
| Screenshot | 画面 A、画面 B、最終結果 | 各 Screenshot が操作中の Binding と結び付く |

完全閉ループでは最低二つの UI Test Case を用意する。

1. `TC-GENERATED`: TestDataPlan が Run 固有 Token を許可済み業務項目へ書き込み、新規データを生成して二画面で同一レコードを操作し、最後に Cleanup する。
2. `TC-ADOPTED`: 事前に被テストシステムへ存在する一件を「既存テストデータ」画面から接管し、既存業務値を変更せず読み取りまたは許可済み操作を行う。対象要件に既存データ利用が不要な場合は別の異常系 Run として実施してよい。

テスト開始前に、被テストシステムへ同じ業務一意値を持つ不要なレコードがないことを確認する。意図的な複数件阻断テストを行う場合だけ重複データを用意し、正常系 Run とは別の Change ID／Run とする。

#### 14.1.2 利用者操作と自動処理の境界を確認する

利用者が行う確認は次の六種類に限定する。

1. 変更要件
2. RAG が選定した設計書
3. 設計書差分
4. コード影響範囲
5. Business Coverage 100% 到達後の最終 UI TestPlan／TestDataPlan
6. 最終レポート

コード編集、Command 実行、Coverage 集計、TestData setup、RunContext 作成、Binding 凍結、Playwright 実行、Screenshot 保存、Cleanup、Closure 集計は自動処理である。利用者が Artifact を Copy、JSON を編集、SQL を貼り付け、Task ID を Chat へ転記しない。Web と VS Code のどちらで確認しても、同じ Stage、Plan Revision、Artifact Digest に対する Confirmation API が一回だけ記録されることを確認する。

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
9. Button が `保存しています` に変わり、連打できないことを確認する。
10. Dialog が閉じ、Project Source の Onboarding が `待機中` または `実行中` になることを確認する。Dialog を開いたまま待つ必要はない。

期待結果:

- Dialog が自動で閉じる。
- 作成した Project が選択される。
- 左側の Project Source に Workspace、UI Test URL、すべての Document Root が表示される。
- Git 管理外 Workspace は `OperaMind 内部 Git` と表示され、初回 Commit が作成される。
- 既存 Git Root は変更を追加 Commit せず、その現在 Commit を基線として表示する。
- Onboarding が `設計書識別`、`Canonical 文書`、`RAG 索引` の順に進み、最後に `準備完了` になる。
- `準備完了` の Project Source に Canonical 設計書数と RAG Vector 数が表示され、どちらも 0 より大きい。
- `事前確認` で Workspace、Document Profile、Embedding Provider が `ready` になる。部分一致または複数 Profile 同点の文書は `review_required` として表示される。
- `設定` で設計書 Folder または UI URL を変更すると設定 Revision が増え、旧 Onboarding は再利用されず新しい再スキャンが開始される。
- `再スキャン` は文書識別から、`再索引` は ready の Document Snapshot から開始される。失敗時は理由と `再試行` が表示される。
- Project Source に `コード品質基線` が表示され、`blocked` の場合は不足項目が初期化通知にも表示される。
- Project を再選択しても同じ情報が表示される。
- Browser を更新しても Project が Database から再読込される。

UI Test で実データを生成または接管する場合は、変更要件を登録する前に管理者／QA が Project の DataIdentityProvider を確認する。通常利用者の「既存テストデータ」画面ではこの設定を編集しない。

1. Project 設定で Provider が今回の Project に属することを確認する。
2. Provider Ref、Provider Type（database／api／ui／hybrid）、Revision、Identity Definition、Lookup Steps、Cleanup Steps を確認する。
3. database はレビュー済み `query_binding_id`、api はレビュー済み API Binding、ui はレビュー済み UI Observation を参照することを確認する。
4. hybrid は最後の Lookup Step が、それ以前の全 Source の同一業務キーを照合してから Identity を確定することを確認する。
5. 保持しないデータには Cleanup Steps があり、任意 SQL、任意 URL、行番号、`nth-child`、曖昧 Text を使用していないことを確認する。
6. Secret はローカル SecretStore にだけ保存され、Project DB、Web 応答、Copilot Context、Evidence から取得できないことを確認する。
7. 未対応 DB 方言の場合は Provider が `blocked` となり、PostgreSQL として推測実行されないことを確認する。

Provider が未確認、Revision が drift、Lookup が 0 件／複数件、または Cleanup が安全に定義できない状態では TestPlan を確認しない。

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
2. UI TestPlan が `schema_version=v2`、`plan_kind=ui`、TestDataPlan が `schema_version=v3` であることを詳細表示で確認する。
3. 各 Case に自然言語の前提条件、操作 Step、期待結果、対応する Business Rule が表示されることを確認する。
4. 各自然言語 Step に、一意な `step_id` を持つ実行可能 UI Step が一件以上対応することを確認する。
5. 全 Business Rule の Coverage が 100% であることを確認する。100% 未満では確認 Button と実行 Buttonが表示されず、未カバー項目が同じ Copilot Change Task へ自動返却されることを確認する。
6. TestDataPlan の各 `test_data_id` に、今回の Project で確認済みの `identity_binding.provider_ref` があることを確認する。別 Project、旧 Revision、存在しない Provider Ref は不合格とする。
7. 複数画面を使う場合、Flow の `depends_on` が画面遷移とデータ依存を表し、後続 Flow／別 Test Case が前序 Flow の同じ `test_data_id` を参照することを確認する。存在しない依存、循環依存、同じ出力変数の上書きは不合格とする。
8. generated データの Setup が許可済み業務項目だけへ Run Token または業務値を書き込み、read-after-write、後置 Assertion、最終 Assertion、逆順 Cleanup を持つことを確認する。
9. adopted データの Setup が既存業務値を変更せず、人工確認済み ExistingTestDataRegistration を参照することを確認する。
10. すべての `bound_record` Step が `data_binding_ref` を持ち、Record Scope の内側に相対 Action Locator を持つことを確認する。
11. Locator が画面構造に応じて role + name、label、placeholder、test id、title、alt text、安全な CSS、または全 `screen_identity_values` の組合せから選択されていることを確認する。`nth()`、`nth-child`、行番号、曖昧 Text、座標、未検証 Dynamic CSS は不合格とする。
12. click、fill、select など状態を変える Action に期待値付き `pre_action_observations`、Action 後 Observation、業務 Assertion、Screenshot 要求があることを確認する。
13. Playwright で実行できる操作に `computer_use_fallback` が付いていないことを確認する。Canvas、Native Dialog、非 DOM Control だけに、理由、Objective、最大操作数、人工確認があることを確認する。Frozen Binding の操作には設定できない。
14. 内容を変更する場合は `自然言語で修正` を押し、Case 名、対象 Step、現在の文言、希望する業務動作、維持する期待結果を入力する。
15. `差分を確認` で変更対象を確認し、正しい場合だけ `この内容を適用` を押す。旧 Plan を直接編集しない。
16. Copilot が完全な新しい UI TestPlan／TestDataPlan を返し、Schema、安全、Business Coverage 100% を再検証することを確認する。
17. 新 Version が完成するまで旧 Version が current で、旧 Run／Evidence／Screenshot が新 Version に流用されないことを確認する。
18. 最終 Plan の業務内容、対象画面、対象データ、操作、Assertion、Cleanup が正しい場合だけ `確認して進む` を押す。

#### 14.10.1 既存データを接管する場合

1. Header の `既存テストデータ` を押す。
2. `データ名`、`業務番号または業務上の一意値`、`このデータを使用する Test Case`、`テスト終了後もこのデータを残す` だけを入力する。
3. SQL、主キー項目、JSON、Locator、Stable Key、内部 Binding ID の入力欄がないことを確認する。
4. `一意性を確認` を押す。
5. database／api／ui Provider は一 Source、hybrid Provider は全 Source の照合が完了するまで待つ。
6. `match_count=1` の場合だけ、脱敏された業務摘要、Provider Type、保持方針、使用 Test Case が表示されることを確認する。
7. 0 件、複数件、Provider 未設定、Source 間の業務キー不一致、Evidence 不足では `blocked` となり、採用確認ができないことを確認する。
8. 一件の業務摘要が意図した実レコードであることを確認し、`確認して採用` を一回だけ押す。
9. 選択中の Change Request 専用 `ui_test_plan_revision` Task が自動発行され、画面に「TestDataPlan の再生成を Copilot に依頼しました」と表示されることを確認する。別 Change Request の登録が Copilot Context に含まれないことも確認する。
10. Copilot が返した完全な UI TestPlan／TestDataPlan v3 が Schema、安全性、Business Coverage 100% と Test Data Coverage 条件の静的対応を再検証し、人工確認を通過するまで実行 Button が有効にならないことを確認する。実 Run 開始後は、実 Observation から算出した Test Data Coverage が 100% になる前に UI Step が開始されないことを確認する。
11. `固定データ識別子` を開き、確認済みの採用予定データと、再確認後の正式 Plan データを区別して確認する。
12. 保持しない場合は同じ `data_binding_ref` を使用する Cleanup が Plan に存在することを確認する。保持する場合は既存業務値を変更しないことを確認する。

#### 14.10.2 generated データが Test Case を実際に覆うことを確認する

1. 各 Acceptance Criterion／Test Case／`test_data_id` の組合せに `coverage_condition` があることを確認する。
2. 正常値、境界値、状態、親子関係など要件に必要な条件が Setup 入力と read-after-write 実測値の両方で表現されることを確認する。
3. 単に Test Case ID や業務 Rule 文言を Evidence へ書いただけの項目は Coverage Proof として数えられていないことを確認する。
4. Setup Output Binding が実レコードの business unique key を返し、Provider Lookup の実測値と一致することを確認する。
5. TestData Coverage が 100% になる前に Playwright が開始されないことを確認する。

### 14.11 TestData と実ブラウザ UI テストを確認する

Plan 確認後、利用者は別 Run、SQL Import、Browser 予行、手動データ投入を開始しない。OperaMind が次の順序を一つの正式 Run と一つの Browser Context で自動実行することを確認する。

1. Run を作成し、`operamind_run_id`、`test_data_token`、`execution_started_at` を生成して read-only で凍結する。
2. `test_data_token` が `OM-E2E-YYYYMMDD-XXXXXX` 形式で、同じ Run 内では不変、過去 Run と重複しないことを確認する。
3. Setup Flow を依存順に実行する。generated は対象システムへ作成または更新し、adopted は確認済み実レコードを読み取る。
4. database／api／ui／hybrid Provider が実レコードを一件に解決し、`project_id`、`run_id`、`test_data_id`、Identity 値、Provider Revision から TestDataBinding を凍結する。
5. `固定データ識別子` を開き、実行後の凍結結果に業務可読値、Provider Type、使用 Test Case／Flow／Step が表示されることを確認する。通常表示に主キー、Locator JSON、Digest、SQL、Secret がないことを確認する。
6. Flow が明示した依存順で実行され、後続 Flow／別 Test Case が同じ Frozen Binding を read-only 参照することを確認する。別 Run／別 Project の Binding、Digest 不一致、上書きは `blocked` とする。
7. Playwright が Project の許可 Origin を開き、一つの Browser Context を維持することを確認する。正式 Action のための別 Browser 予行 Run がないことを確認する。
8. 各状態変更 Action の直前に、ページ Origin、画面状態、Record Scope、Action Locator を只読検証することを確認する。
9. `bound_record` では Frozen Binding の全業務一意キーと全 `screen_identity_values` を実 DOM から読み、`record_scope_match_count=1`、`action_locator_match_count=1`、再計算した `observed_identity_digest` の一致後だけ Action を実行することを確認する。
10. 一覧画面から詳細画面へ遷移した後も、同じ `test_data_id`／Binding の業務キーが表示され、別レコードを操作していないことを確認する。
11. Action 後の Observation と業務 Assertion が実測値で成功し、各 Step に Step Log、driver、Locator Type、Scope／Action 件数、Binding Ref が記録されることを確認する。
12. Case ごと、または Plan 指定 Step ごとに Screenshot が保存され、撮影時に操作していた Binding と関連付くことを確認する。
13. Password、Token、接続情報、Secret 項目、マスク対象個人情報が Screenshot、DOM Observation、Step Log、Evidence にないことを確認する。
14. すべての業務 Step 後、Cleanup を逆依存順に実行する。業務 Assertion が失敗した場合も、安全に識別できる範囲で Cleanup を試行することを確認する。
15. UI Cleanup は元の Frozen `record_scope_locator` を使用し、Action 前に一件、業務 Identity Digest 一致、Action Locator 一件を再確認してから Scope 内だけで削除することを確認する。
16. UI Cleanup 後に同じ Scope の一致数が 0 件であることを確認する。database／api Provider がある場合は、同じ business unique key が Provider 側でも 0 件であることを確認する。
17. Cleanup 完了後、Web に Step ごとの成功／失敗、Run 変数、Assertion、Screenshot、Cleanup Result、停止理由が表示されることを確認する。

次のいずれかが起きた場合は Action を実行せず `blocked` にする。

- Record Scope または Action Locator が 0 件／複数件
- DOM の業務 Identity 値が欠落または Frozen Binding と不一致
- Origin が Project 設定外
- Binding が別 Project／別 Run、または Digest が不一致
- DOM Structure drift

Blocked 時は脱敏 Screenshot、Locator Type、実 match count、公開可能な DOM Observation、失敗工程を保存する。OperaMind が同じ Change Task に簡潔な失敗情報を記録し、新しい `ui_test_plan_revision` Task を自動発行することを確認する。Copilot は業務期待値を変えずに完全な Plan Revision を作り、新 Revision は Schema／安全検証と人工確認を再度通過する。元 Run の途中で Locator を差し替えて続行せず、Computer Use で Binding 対象を推測操作しない。

### 14.12 最終レポートと再起動耐性を確認する

1. `最終レポート` を開く。
2. Requirement、設計書差分、Scope、Code Diff、Command、Coverage、UI TestPlan、TestDataPlan、Screenshot が同一 Project、Change ID、Result Revision、Plan Revision、Run ID に結び付いていることを確認する。
3. 各 Business Rule から `Business Rule → Test Case → UI Step → TestDataBinding → DataIdentityProvider → 実レコード → Assertion／Screenshot → Cleanup` を順に開けることを確認する。
4. Business Coverage が 100%、Test Data Coverage が 100%、Changed-line Coverage が最低基準以上であることを確認する。
5. Modified Path が確認済み Code Scope 内、必須 Command がすべて成功、Screenshot と Cleanup Result が現在 Run の Binding に解決することを確認する。
6. unresolved item と blocking reason が 0 件で、ChangeClosureResult が `passed` であることを確認する。
7. `確認して進む` を一回だけ押し、全体 Status が `完了` になることを確認する。
8. Browser を更新し、完了状態と成果物が維持されることを確認する。
9. OperaMind Web を再起動し、同じ Project と Change ID を選択して完了状態が復元されることを確認する。
10. VS Code を再起動して `OperaMind: 現在のタスクを再開` を実行し、完了 Task が再編集されず、履歴として表示されることを確認する。
11. 過去の途中状態や古い Copilot Task の失敗が現在工程を上書きしないことを確認する。

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

失敗または停止した場合も、その時点までの合格項目と最初の停止理由を残す。DB を直接補正して同じ実行を成功扱いにしない。Locator Revision、Copilot 再生成、明示的な Retry が製品フローとして提供される場合は同じ Change ID の履歴を保持したまま新しい Plan Revision／Run で再実行する。要件自体を変更した場合、基線を汚染した場合、または安全な再開点を証明できない場合だけ、新しい Change ID で最初から実行する。

### 14.14 完全閉ループの状態遷移チェック表

テスト中は「処理が動いているように見える」だけで合格にせず、`現在のアクション` が次の順序で遷移することを記録する。公開六工程の内側にある自動 Stage は詳細／履歴で確認する。

| No. | 内部 Stage／現在のアクション | 主体 | 利用者操作 | 次へ進む条件 |
|---:|---|---|---|---|
| 1 | `requirement_confirmation` | 利用者 | 変更要件を確認 | 対象 Project と要件 Digest が一致 |
| 2 | `rag_document_confirmation` | 利用者 | RAG 文書と根拠を確認 | Canonical 原本へ復元でき、別 Project 混入なし |
| 3 | `document_generation` | VS Code GitHub Copilot | なし | Scope 内設計書差分を記録 |
| 4 | `document_confirmation` | 利用者 | 設計書差分を確認 | 業務要件と差分が一致 |
| 5 | `impact_analysis` | Copilot／OperaMind | なし | Code Graph と Test Binding を含む Impact が完成 |
| 6 | `impact_confirmation` | 利用者 | コード影響範囲を確認 | production／test／UI／設定 Scope が妥当 |
| 7 | `execution_approval` | OperaMind | なし | 確認済み Scope から Edit Packet／Grant を自動生成 |
| 8 | `code_change` | VS Code GitHub Copilot | なし | Scope 内 Diff、compile、test、coverage が成功して Result を固定 |
| 9 | `planning` | Copilot／OperaMind | なし | UI TestPlan v2、TestDataPlan v3、Business Coverage 100% |
| 10 | `test_plan_confirmation` | 利用者 | 最終 Plan と実行範囲を確認 | Revision／Digest が現在 Plan と一致 |
| 11 | `test_data_execution` | OperaMind | なし | Setup／adopt、Binding 凍結、Test Data Coverage 100% |
| 12 | `ui_verification` | Playwright | なし | Action 前検証、跨画面 Assertion、Screenshot、Cleanup 成功 |
| 13 | `closure` | OperaMind | なし | 全 Evidence を同じ Project／Revision／Run に結合 |
| 14 | `final_report_confirmation` | 利用者 | 最終レポートを確認 | Closure `passed`、unresolved 0 件 |
| 15 | `completed` | OperaMind | なし | 完了状態を永続化し、再起動後も復元 |

各行で次の四点をテスト記録へ残す。

1. Stage 開始／終了時刻
2. Web に表示された業務可読な現在アクション
3. 確認に使用した Revision／Digest の非 Secret 摘要
4. 成功 Evidence、または最初の Blocking Reason

次の遷移は不合格とする。

- RAG 文書確認前に設計書を変更する
- 設計書差分確認前に Code Scope を確定する
- Code Scope 確認前に production code を変更する
- compile／test／changed-line Coverage 成功前に TestPlan を生成・確認する
- Business Coverage 100% 前に人工確認または UI Test を開始する
- Test Data Coverage 100% 前に Playwright を開始する
- Frozen Binding の Action 前検証に失敗したまま操作を続行する
- Cleanup または Screenshot Evidence が失敗したまま Closure を `passed` にする
- 最終レポート確認前に全体 Status を `完了` にする

### 14.15 正常系一回分の最終チェックリスト

次を上から順に一項目ずつ確認し、空欄を残さない。

- [ ] 空 Database または今回使用する Project 基線を確認した
- [ ] OperaMind Web、VS Code Bridge、GitHub Copilot、対象システムが利用可能である
- [ ] Code Workspace と全 Document Root を Web から登録した
- [ ] Git／内部 Git、Canonical Snapshot、RAG Index、Code Quality Baseline が ready である
- [ ] 対象 DataIdentityProvider と必要な Target Data Binding が確認済みである
- [ ] 変更要件と RAG 対象設計書を人工確認した
- [ ] Copilot が Scope 内の設計書だけを変更した
- [ ] 設計書差分と Code Impact Graph を人工確認した
- [ ] Copilot が Scope 内のコードとコードテストだけを変更した
- [ ] Compile、Test、Changed-line Coverage が現在 Result Revision で成功した
- [ ] UI TestPlan v2 と TestDataPlan v3 が生成された
- [ ] Business Coverage が 100% になった後だけ最終 Plan を人工確認した
- [ ] RunContext の三つの System Variable が一意かつ read-only である
- [ ] generated／adopted データが実 Provider で一件に解決し Frozen Binding になった
- [ ] Test Data Coverage が実測 Evidence で 100% になった
- [ ] Playwright が Action 前に実 DOM と Frozen Binding を照合した
- [ ] 複数画面／複数 Flow が同じ業務レコードを操作した
- [ ] 各 UI Step に Assertion、Step Log、Binding、Screenshot がある
- [ ] Cleanup が元 Binding を使用し、UI と利用可能な DB／API で 0 件を確認した
- [ ] Secret が DB、Log、Copilot Context、Evidence、Screenshot にない
- [ ] 最終追跡 Chain が全要件から Cleanup まで解決できる
- [ ] ChangeClosureResult が `passed`、Business Coverage 100%、Test Data Coverage 100% である
- [ ] 最終レポートを人工確認し、全体 Status が `完了` になった
- [ ] Browser／OperaMind／VS Code 再起動後も同じ完了状態を復元できた
