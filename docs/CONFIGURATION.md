# OperaMind 設定ガイド

この文書は、OperaMind を起動し、一つの Project を変更閉ループへ接続するために必要な設定をすべて説明する。実値、Password、API Key、Bridge Token は例へ置き換え、Git、Copilot Context、ログ、Evidence、テスト記録へ転記しない。

## 1. 設定の種類

OperaMind には三種類の設定がある。保存場所と用途を混同しない。

| 種類 | 保存場所 | 主な値 |
|---|---|---|
| 端末の実行設定 | 配布版 `config.env`、ソース版 `.env` | OperaMind DB、Embedding、Playwright、Task 並列数 |
| Project 設定 | Web の「新しいプロジェクト／プロジェクト設定」 | Workspace、設計書 Folder、被テスト UI URL、Target Data Profile |
| 自動生成 Secret／Runtime | ユーザー領域 | Bridge Token、VS Code MCP runtime、被テスト DB Secret |

`config.env`／`.env` は全 Project 共通である。Workspace、設計書 Folder、UI URL、被テスト DB は Project ごとに異なるため、環境変数ではなく Web から登録する。

## 2. 設定ファイルの場所と優先順位

### 2.1 配布版

| OS | ユーザー設定 | 自動生成 Runtime |
|---|---|---|
| Windows | `%LOCALAPPDATA%\OperaMind\config.env` | 同 Folder の `runtime.json`、`bridge-token`、`target-data-secrets` |
| macOS | `~/Library/Application Support/OperaMind/config.env` | 同 Folder の `runtime.json`、`bridge-token`、`target-data-secrets` |
| Linux | `${XDG_DATA_HOME:-~/.local/share}/operamind/config.env` | 同 Folder の `runtime.json`、`bridge-token`、`target-data-secrets` |

配布版は `OperaMind.app` または `OperaMind.exe` を起動する。Launcher が migration、Web、Browser、VS Code 用 runtime を準備するため、利用者は `migrate`、`web`、`mcp` を個別起動しない。

### 2.2 ソース版

Repository Root で `.env.example` を `.env` へコピーする。

macOS／Linux:

```bash
cp .env.example .env
.venv/bin/operamind-launcher --root .
```

Windows PowerShell:

```powershell
Copy-Item .env.example .env
.\.venv\Scripts\operamind-launcher.exe --root .
```

### 2.3 優先順位

同じ名前が複数箇所にある場合、次の順で最初の値を使用する。

1. Launcher を起動した Process に既に存在する OS 環境変数
2. ユーザー領域の `config.env`
3. ソース Root の `.env`

後順位のファイルは前順位の値を上書きしない。変更後は OperaMind を再起動する。

### 2.4 ファイル形式

- 一行に `NAME=value` を一つ記述する。
- `export NAME=value`、単一引用符、二重引用符を使用できる。
- UTF-8、UTF-8 BOM、LF、CRLF を使用できる。
- 同じファイル内の重複名、不正な変数名、閉じていない引用符は起動エラーになる。
- Shell 展開は行わない。`$HOME` や `${PASSWORD}` は文字列のままになるため、完成した値を書く。
- 実値を含む `.env` と `config.env` を Git に追加しない。

## 3. 端末の実行設定

### 3.1 製品 Runtime で使用する値

| 設定名 | 必須 | 既定値 | 設定方法と用途 |
|---|---:|---|---|
| `OPERAMIND_DATABASE_URL` | 必須 | なし | OperaMind 自身の PostgreSQL 18 + pgvector 接続 URL。被テストシステム DB ではない |
| `EMBED_API_URL` | RAG 使用時必須 | なし | `profiles/embedding-profile.example.json` が参照する OpenAI-compatible Embedding API Base URL |
| `EMBED_API_KEY` | RAG 使用時必須 | なし | Embedding Provider の Key。LM Studio のローカル互換 API でも空文字は禁止 |
| `EMBED_MODEL` | RAG 使用時必須 | なし | Provider が実際に返す Model ID と完全一致させる |
| `OPERAMIND_MAX_ACTIVE_TASKS_PER_RUN` | 任意 | `1` | 一つの Automation Run で同時に active にできる内部 Task 数。整数 `1`～`100` |
| `OPERAMIND_PLAYWRIGHT_CHANNEL` | 任意 | Windows `msedge`、その他 `chrome` | UI Test に使用するインストール済み Browser。配布版の想定値は `chrome` または `msedge` |

`OPERAMIND_BRIDGE_TOKEN` は利用者設定ではない。Launcher が owner-only file を生成し、VS Code Extension が SecretStorage へ同期する。`.env`、`config.env`、DB、チャットへ書かない。

### 3.2 最小設定例

macOS のローカル PostgreSQL:

```dotenv
OPERAMIND_DATABASE_URL=postgresql:///operamind_vnext?host=/private/tmp&port=5432
OPERAMIND_MAX_ACTIVE_TASKS_PER_RUN=1
EMBED_API_URL=http://127.0.0.1:1234/v1
EMBED_API_KEY=lm-studio
EMBED_MODEL=text-embedding-nomic-embed-text-v1.5
OPERAMIND_PLAYWRIGHT_CHANNEL=chrome
```

Windows のローカル PostgreSQL:

```dotenv
OPERAMIND_DATABASE_URL=postgresql://operamind:<PASSWORD>@127.0.0.1:5432/operamind_vnext
OPERAMIND_MAX_ACTIVE_TASKS_PER_RUN=1
EMBED_API_URL=http://127.0.0.1:1234/v1
EMBED_API_KEY=lm-studio
EMBED_MODEL=text-embedding-nomic-embed-text-v1.5
OPERAMIND_PLAYWRIGHT_CHANNEL=msedge
```

`<PASSWORD>` は実 Password に置き換える。Windows では Unix socket URL を使用しない。Password に `@`、`:`、`/` などがある場合は URL encode する。

## 4. OperaMind 自身の PostgreSQL

OperaMind の Canonical 文書、RAG Vector、Code Graph、Task、Approval、Evidence metadata は PostgreSQL に保存する。`pgvector` と PostgreSQL 固有の migration／lock／JSONB を使用するため、この DB は Oracle、MySQL、SQLite へ切り替えない。

準備条件:

1. PostgreSQL 18 を起動する。
2. Server に pgvector Extension をインストールする。
3. `operamind_vnext` Database と接続 User を作成する。
4. User に Database の migration と `CREATE EXTENSION vector` に必要な権限を与える。権限を限定する場合は管理者が先に同 Database へ `vector` Extension を作成する。
5. `OPERAMIND_DATABASE_URL` を設定して Launcher を起動する。

確認方法:

- Launcher が `http://127.0.0.1:8765/health` を公開する。
- 起動時に migration エラーが表示されない。
- Web のローカル診断で Database が `ready` になる。

OperaMind DB と被テストシステム DB に同じ Database／User を使わない。Test Data cleanup や誤設定の影響範囲を分離する。

## 5. Embedding の設定

既定 Profile は `profiles/embedding-profile.example.json` であり、環境変数の「名前」だけを保持する。API Key 自体は Profile に書かない。

1. LM Studio などの OpenAI-compatible Embedding Server を `EMBED_API_URL` で起動する。
2. `EMBED_MODEL` に実際に load した Model ID を設定する。
3. Profile の `expected_dimensions` と実 Vector 次元を一致させる。既定は `768`。
4. OperaMind を再起動し、Project の「事前確認」で Embedding Provider の Model と次元を確認する。

Base URL は `/v1` までを指定する。Provider が返した Model ID、件数、次元が設定と異なる場合は RAG Onboarding を blocked にする。API response body や Key をログへ出さない。

## 6. Browser／Playwright の設定

1. 被テストシステムへ接続できる Chrome または Edge をインストールする。
2. 必要な場合だけ `OPERAMIND_PLAYWRIGHT_CHANNEL=chrome` または `msedge` を設定する。
3. Project 設定の「UI テスト対象 URL」に被テストシステムの Origin を入力する。

URL の条件:

- `http://` または `https://` の絶対 URL
- 例: `http://127.0.0.1:8080`
- User／Password、Query、Fragment を含めない
- OperaMind Web の `http://127.0.0.1:8765` を入力しない

UI URL は Project 固有であり、グローバル環境変数ではない。`OPERAMIND_TEST_TARGET_BASE_URL` は製品設定として存在せず、設定しても Project には反映されない。

## 7. Project 設定

Web の「新しいプロジェクト」で次を設定する。

| 画面項目 | 必須 | 説明 |
|---|---:|---|
| Project ID | 必須 | 英数字で開始し、英数字、`.`、`_`、`-` を使用する安定 ID |
| Project 名 | 必須 | 画面表示名 |
| コード Workspace | 必須 | 被変更コードの OS ネイティブ絶対 Path。登録後は Evidence identity として固定 |
| 設計書の場所 | 必須 | 一行に一つの絶対 Folder Path。最大 20 件。コード Repository 外でもよい |
| UI テスト対象 URL | UI Test 時必須 | 被テストシステムの credential-free Origin |
| 被テストシステム DB データ準備 | SQL 使用時必須 | 次章の PostgreSQL Target Data Profile |

コードまたは設計書が Git 管理外の場合、OperaMind は外部送信しないローカル Git baseline を作る。既存 Git Repository に未 Commit 変更がある場合は勝手に baseline を更新しない。

設定後は「事前確認」で Workspace、Document Profile、Embedding、Browser の状態を確認する。`blocked` の項目がある状態で変更要件を開始しない。

### 7.1 被テスト工程の Compile／Test 環境

Compile、Test、Coverage は Project の確認済み `CommandExecutionProfile` が固定 argv、作業 Folder、timeout、許可する環境変数名を決める。OperaMind 全体の `.env` に各 Project の Build 設定を無制限に追加しない。

既定 Java／Gradle Profile が許可する主な値は `PATH`、`HOME`、`JAVA_HOME`、`GRADLE_USER_HOME`、`LANG` である。例えば Spring Boot 1.5 Project では、VS Code／OperaMind を起動した環境の `JAVA_HOME` をその Project が対応する JDK へ向ける。Profile の `environment_keys` にない変数は Command へ渡らない。新しい技術 Stack では、実行コマンドと必要な環境変数名を新しい確認済み Profile として追加し、任意 Shell command や全環境の引継ぎを許可しない。

## 8. 被テストシステム DB データ準備

### 8.1 現在の対応範囲

現在、production で実行できる SQL Target Data 方言は PostgreSQL のみである。画面の「PostgreSQL 接続 Secret」へ Oracle、MySQL、SQLite の接続値を入力すると拒否する。Oracle 対応状態は第 10 章を参照する。

### 8.2 接続設定

| 項目 | 入力例 | 意味 |
|---|---|---|
| 接続 Alias | `expense_test_db` | Project 内で安定した論理名。英字または `_` で開始し、英数字と `_` のみ |
| Database 方言 | `postgresql` | 登録済み `TargetDatabaseAdapter` の key。現在 Web で選べる値は PostgreSQL だけ |
| PostgreSQL 接続 Secret | `postgresql://tester:<PASSWORD>@127.0.0.1:5432/expense_test` | Password を含む TCP URL。対象システム専用 User／Database を使う |
| Transaction Policy | `per_binding_transaction` | 現在の唯一の値。一 Binding の statement、readback、検査を一 Transaction で実行 |
| SQL Binding | JSON 配列 | AI が参照できる確認済み操作。任意 SQL は受理しない |

接続 Secret はユーザー領域の `target-data-secrets` に SHA-256 名の owner-only file として保存する。画面、API response、OperaMind DB、Copilot Context、ログ、Evidence へ戻さない。Project 設定を再保存するとき、Secret 欄を空にすると既存 Secret を保持する。

### 8.3 SQL Binding の項目

| 項目 | 条件 |
|---|---|
| `query_binding_id` | Project 内で一意の安全な名前 |
| `operation` | `write`、`read`、`cleanup` |
| `statement_text` | 一つの確認済み SQL statement。複数 statement と任意 SQL input は禁止 |
| `target_schema`／`target_table` | 実在する安全な識別子 |
| `parameter_columns` | named parameter と対象 Column の一対一 Mapping |
| `input_constraints` | 全 parameter の型、必須、長さ、pattern、enum、最小／最大 |
| `read_after_write_statement` | 同じ parameter で実データを回読する一 statement |
| `read_assertion` | `rows_present`、`rows_absent`、または `row_count` |
| `identity_contract` | Primary Key、業務 Unique Key、画面 Key、Coverage Column |
| `cleanup_binding_id` | write Binding が必ず参照する確認済み cleanup Binding |
| `idempotency_policy` | `natural_key`、`upsert`、`delete_then_insert`、`read_only` |

PostgreSQL parameter は `%(expense_id)s` のような named placeholder を使用する。文字列結合、位置 parameter、Parameter 名と Column／Constraint の不一致は拒否する。

### 8.4 完全な例

```json
[
  {
    "query_binding_id": "upsert_expense",
    "operation": "write",
    "statement_text": "INSERT INTO expenses (expense_id, status) VALUES (%(expense_id)s, %(status)s) ON CONFLICT (expense_id) DO UPDATE SET status = EXCLUDED.status",
    "target_schema": "public",
    "target_table": "expenses",
    "parameter_columns": {
      "expense_id": "expense_id",
      "status": "status"
    },
    "input_constraints": {
      "expense_id": {
        "type": "string",
        "required": true,
        "max_length": 20,
        "pattern": "^EXP-[0-9]{3}$"
      },
      "status": {
        "type": "string",
        "required": true,
        "max_length": 20,
        "enum": ["DRAFT", "SUBMITTED"]
      }
    },
    "read_after_write_statement": "SELECT expense_id, expense_number, status FROM expenses WHERE expense_id = %(expense_id)s AND status = %(status)s",
    "read_assertion": {
      "mode": "row_count",
      "count": 1
    },
    "identity_contract": {
      "primary_key": "expense_id",
      "business_unique_keys": ["expense_number"],
      "screen_key": "expense_number",
      "coverage_columns": ["status"]
    },
    "cleanup_binding_id": "cleanup_expense",
    "idempotency_policy": "upsert"
  },
  {
    "query_binding_id": "cleanup_expense",
    "operation": "cleanup",
    "statement_text": "DELETE FROM expenses WHERE expense_id = %(expense_id)s AND status = %(status)s",
    "target_schema": "public",
    "target_table": "expenses",
    "parameter_columns": {
      "expense_id": "expense_id",
      "status": "status"
    },
    "input_constraints": {
      "expense_id": {
        "type": "string",
        "required": true,
        "max_length": 20,
        "pattern": "^EXP-[0-9]{3}$"
      },
      "status": {
        "type": "string",
        "required": true,
        "max_length": 20,
        "enum": ["DRAFT", "SUBMITTED"]
      }
    },
    "read_after_write_statement": "SELECT expense_id FROM expenses WHERE expense_id = %(expense_id)s AND status = %(status)s",
    "read_assertion": {
      "mode": "rows_absent"
    },
    "identity_contract": {
      "primary_key": "expense_id",
      "business_unique_keys": ["expense_number"],
      "screen_key": "expense_number",
      "coverage_columns": ["status"]
    },
    "cleanup_binding_id": null,
    "idempotency_policy": "natural_key"
  }
]
```

保存時と実行時に実 Table／Column、型、長さ、Primary Key、業務 UNIQUE Constraint を再確認する。write 後は readback が成功し、Identity 用 readback はちょうど一件でなければ UI Test を開始しない。

### 8.5 確認済み DataIdentityProvider

Project 管理者は「管理者向け高度設定：被テストシステム DB」の「確認済み DataIdentityProvider」へ JSON 配列を登録する。この設定は普通テスト担当者の主フローには表示されない。普通テスト担当者は後述の「既存テストデータを登録」で業務値だけを入力する。

各 Profile の必須項目は次のとおり。

| 項目 | 説明 |
|---|---|
| `provider_ref` | Project 内で固定する Provider Version。例 `database.expense.v1` |
| `provider_type` | `database`、`api`、`ui`、`hybrid` のいずれか |
| `lookup_steps` | 管理者がレビューした照会 Step。利用者入力は `{{business_unique_value}}` にだけ差し込む |
| `cleanup_steps` | 同じ Binding を使用する確認済み cleanup。保持するデータでは空配列でもよい |
| `identity_definition` | Primary Key、業務一意キー、画面キー、match count の実観測 Path |
| `business_summary_fields` | 普通画面へ脱敏表示してよい業務項目名 |

Database Profile の最小例を示す。`read_expense_by_number` は第 8.3 節で登録済みの read 専用 `query_binding_id` であり、ここへ SQL 本文を書かない。

```json
[
  {
    "provider_ref": "database.expense.v1",
    "provider_type": "database",
    "lookup_steps": [
      {
        "step_id": "lookup-expense",
        "sequence": 1,
        "channel": "sql",
        "business_action": "業務番号で既存経費を確認する",
        "target": "read_expense_by_number",
        "inputs": {"expense_number": "{{business_unique_value}}"},
        "depends_on": [],
        "output_bindings": [],
        "postconditions": [
          {
            "assertion_id": "lookup-expense-unique",
            "observe_via": "database",
            "subject": "row_count",
            "operator": "count_equals",
            "expected": 1
          }
        ]
      }
    ],
    "cleanup_steps": [],
    "identity_definition": {
      "source_step_id": "lookup-expense",
      "primary_key": {"name": "id", "source": "database", "path": "rows[0].id"},
      "business_unique_keys": [
        {
          "name": "expense_number",
          "source": "database",
          "path": "rows[0].expense_number",
          "dom_observation": {"kind": "attribute", "attribute_name": "data-expense-number"}
        }
      ],
      "screen_key": {
        "name": "expense_number",
        "source": "database",
        "path": "rows[0].expense_number",
        "dom_observation": {"kind": "attribute", "attribute_name": "data-expense-number"},
        "locator_template": {"by": "css", "value": "[data-expense-number='{{value}}']", "exact": true}
      },
      "match_count": {"source": "database", "path": "row_count"}
    },
    "business_summary_fields": ["expense_number", "status"]
  }
]
```

- `api` は `channel=http` と確認済み API Binding を使い、response の実件数と実業務値を観測する。
- `ui` は `channel=ui` と確認済み Playwright Observation を使い、画面上の件数と DOM attribute の実業務値を観測する。
- `hybrid` は SQL／HTTP／UI のうち二つ以上の実 Source を実行し、同名・同値の業務一意キーで同一レコードを証明する。一 Source の結果を他 Source の観測値として複製しない。
- 全 Type で `match_count=1` と Evidence が必要である。0 件、複数件、Provider／Executor 未設定、Source 間不一致、Evidence 不足は blocked となる。別 Provider や PostgreSQL へ静かに切り替えない。
- SQL、接続 Secret、内部 Binding ID は管理者設定または内部 Artifact に限定し、普通画面、Copilot Chat の要約、Evidence の業務摘要へ出さない。確認済み Locator／Identity 定義は計画生成に必要な範囲だけ MCP の構造化 Planning Input へ渡すが、Chat の通常表示では「詳細」に隠す。
- `lookup_steps` と `cleanup_steps` は TestDataPlan の実行 Step と同じ `business_action`、連番 `sequence`、依存関係、入力、出力、実観測 `postconditions` を持つ。空の Assertion は保存時に拒否される。
- `identity_definition.source_step_id` は最後の lookup Step を指す。特に hybrid は必要な全 Source の Evidence が揃う前に Binding を凍結できない。
- 「終了後も保持しない」既存データを採用する Profile は `cleanup_steps` が必須である。OperaMind は採用時に業務一意値を固定し、同じ `test_data_id` の `data_binding_ref` を自動付与する。UI cleanup は Frozen record Scope 内だけで操作し、`cleanup_record_scope_match_count=0` を確認する。Database／API Source がある場合は、対応する確認済み cleanup Binding の readback でも 0 件を証明する。

### 8.6 既存テストデータの普通利用者操作

1. TestPlan に対象 Test Case が生成された後、Project の「既存テストデータ」を開く。
2. 「データ名」「業務番号または業務上の一意値」「使用する Test Case」「終了後も保持するか」だけを入力する。
3. 「一意性を確認」を押す。OperaMind が Project の確認済み Provider を実行する。
4. `1 件` の候補と脱敏済み業務摘要を確認し、「確認して採用」を押す。
5. 確認すると、その登録を選択中の Change Request に固定し、Copilot へ完全な UI TestPlan／TestDataPlan v3 の再生成 Task を自動発行する。別 Change Request の登録は現在 Plan に混入しない。
6. 再生成された Plan は Schema、安全性、Business Coverage 100% と Test Data Coverage 条件の完全な静的対応を再検証する。人工確認を通過した後だけ正式な `binding_mode=adopted` TestDataPlan となり、実 Run では各条件を実 Observation から評価して Test Data Coverage 100% に達した後だけ UI Step へ進む。確認前の候補や再生成前の旧 Plan は実行できない。
7. 「固定データ識別子」で採用待ち／計画済みデータを確認する。実行後は同じ画面で Run、利用 Test Case／Flow／Step、Evidence、Cleanup 結果を確認する。

候補生成時には入力した業務一意値と Provider が返した実業務キーを照合し、Provider の Revision と設定 Digest を候補へ固定する。人工確認時も同一 Transaction 内で現在の Provider 行を Lock して Revision と Digest を再照合する。候補生成後または確認直前に Provider 設定が変わった場合は確認を拒否するため、利用者は一意性確認から再登録する。確認済みデータの Test Case が現在の UiTestPlan に存在しない場合は、入力ミスや古い Case を静かに無視せず Plan 受理を blocked にする。確認済みデータが対象 Test Case に対応する場合、Copilot の TestDataPlan は固定済み `test_data_id`、`identity_binding`、lookup／cleanup Flow を実際に組み込まなければ受理されない。実行開始時にも現在 Change Request の全確認済み登録を再検証するため、採用前の旧 Plan をそのまま実行できない。

`adopted` データは既存業務値を既定で変更しない。更新が必要な Case は、TestDataPlan が書込みを許可した業務項目と確認済み Binding を別途持つ必要がある。Run ごとの `operamind_run_id`、`test_data_token`、`execution_started_at` は OperaMind が生成して凍結する読み取り専用値であり、利用者、Copilot、Flow の局所変数で上書きできない。

## 9. VS Code GitHub Copilot Bridge

1. 配布 ZIP と同じ Version の `operamind-copilot-bridge.vsix` を VS Code へ一度インストールする。
2. OperaMind Launcher を起動する。
3. Project のコード Workspace を VS Code で開き、Workspace Trust を有効にする。
4. Extension の診断画面で OperaMind Web、MCP、GitHub Copilot を確認する。

Launcher は `runtime.json` に MCP command、引数、Web URL、Bridge Token file の Path を書く。Windows の `OperaMindMcp.exe` は VS Code が stdio child として起動するため、利用者は直接起動しない。Bridge Token の内容を表示、コピー、設定しない。

## 10. Oracle と複数 Database Adapter

### 10.1 結論

多 Database 対応を考慮する必要があるのは「被テストシステム DB」だけである。OperaMind 自身の DB は PostgreSQL + pgvector のままにする。被テストシステム側は `TargetDatabaseAdapter`、Dialect Registry、共通実行結果へ分離済みであり、Profile／Secret／Binding／実行器は同じ Dialect key を使う。次 Project が Oracle の場合は Oracle Target Data Adapter を実装して Registry に登録してから接続する。現在の画面や API に Oracle DSN を入れても動作しない。

### 10.2 Oracle Adapter に必要な実装

Oracle 対応は次を一組として実装する。

1. `TargetDatabaseAdapter` を実装し、既定 Registry へ `dialect=oracle` として登録
2. `python-oracledb` の固定 Version と配布物への Driver 同梱
3. User、Password、DSN／Service Name を分離した owner-only Secret 形式
4. Oracle named bind `:expense_id` の厳格な parameter 検査
5. `ALL_TAB_COLUMNS`／`ALL_CONSTRAINTS`／`ALL_CONS_COLUMNS` による実 Column、PK、UNIQUE 検査
6. 大文字化される unquoted identifier、Schema Owner、Synonym の明示的処理
7. `NUMBER`、`VARCHAR2`、`CHAR`、`DATE`、`TIMESTAMP`、`CLOB` の型 Mapping
8. PostgreSQL `ON CONFLICT` ではなく、確認済み Oracle `MERGE` 等の Binding
9. Binding 単位 Transaction、rollback、read-after-write、cleanup、冪等性
10. Oracle error code を Secret なしの分類へ変換する処理
11. DataIdentityProvider の `match_count=1` と UI DOM identity の実照合
12. 実 Oracle Database を使う migration-free Integration／UI 閉ループ回帰

Control DB は安全な Dialect key を保存できるため、Oracle 追加時に新しい Control DB migration や主フロー分岐は不要である。ただし実 Adapter、Driver、実 DB Evidence が揃う前に Web の選択肢へ `oracle` を追加しない。未登録方言は Profile／Secret 保存時に拒否する。既存データ等から未登録方言が参照された場合も、未登録方言は Plan 確認前に blocked とし、PostgreSQL へ fallback しない。

## 11. 開発／受入テスト専用設定

次は製品 Runtime の通常設定ではない。

| 設定名 | 用途 |
|---|---|
| `OPERAMIND_TEST_DATABASE_URL` | pytest がランダム名の一時 PostgreSQL Database を作成／削除するための管理接続。開発 DB を指定しない |
| `OPERAMIND_EMBEDDING_LIVE=1` | 実 Embedding Provider Integration Test を明示的に有効化 |
| `OPERAMIND_EMBEDDING_LIVE_PROFILE` | 上記 Test が読む検証済み Embedding Profile Path |
| `OPERAMIND_PLAYWRIGHT_EXECUTOR_LIVE=1` | 実 Browser Executor Test を有効化 |
| `OPERAMIND_PLAYWRIGHT_LIVE=1` | Main Change Flow の実 UI Test を有効化 |

Test 用環境変数を設定しない場合、対応する live test は skip される。skip を production 成功 Evidence として扱わない。

## 12. 設定後の確認と障害切り分け

### 12.1 確認順序

1. `OPERAMIND_DATABASE_URL` と PostgreSQL／pgvector
2. Launcher と `http://127.0.0.1:8765/health`
3. Embedding API の Model／次元
4. Web の Project Workspace／設計書 Folder
5. Project Onboarding／RAG `ready`
6. VS Code Extension／MCP／GitHub Copilot
7. 被テスト UI URL と Browser
8. 必要な Project だけ Target Data PostgreSQL Profile／Secret／Binding

### 12.2 よくある停止理由

| 症状 | 確認内容 |
|---|---|
| `OPERAMIND_DATABASE_URL is required` | 設定ファイル Path、優先順位、変数名、OperaMind 再起動 |
| migration が失敗 | PostgreSQL Version、pgvector、User 権限、接続先 Database |
| Embedding が blocked | API 起動、Base URL、Key、Model ID、768 dimensions |
| Browser が見つからない | Chrome／Edge のインストールと `OPERAMIND_PLAYWRIGHT_CHANNEL` |
| UI Test の `base_url` がない | 環境変数ではなく Project 設定の UI テスト対象 URL |
| SQL Plan が blocked | PostgreSQL Target Data Profile、Secret、Binding ID、cleanup、readback |
| Identity が 0 件／複数件 | 生成データ、業務 UNIQUE Key、readback 条件、画面検索条件を修正 |
| Oracle URL が拒否される | 現在は未対応。Oracle Adapter を実装／検証するまで PostgreSQL fallback は行わない |

Secret を確認するために内容をログへ出力しない。画面の `secret_configured`、接続診断、Secret を含まない error category で判定する。
