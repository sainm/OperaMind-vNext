# OperaMind Server 版将来構想

## 状態

この文書は将来の Server 版に対する設計判断を固定するものであり、**現時点では実装しない**。現在の製品契約は、`127.0.0.1` だけで動作する認証なしの単機 Desktop 版である。本構想を理由に、現在の Launcher、Web、MCP、VS Code Extension、Database migration、RAG、Playwright の挙動を変更しない。

実装を開始するときは、設計、脅威分析、Artifact Contract、migration、配布物、回帰範囲を改めて確認し、一括置換ではなく段階的に移行する。

## 決定済みの境界

Server 版は次の三コンポーネントへ分離する。

| コンポーネント | 責務 |
|---|---|
| OperaMind Server | Web／API、ユーザー認証、Organization／Project 権限、PostgreSQL／pgvector、Canonical 文書片、Embedding、RAG、Workflow、Confirmation、Task Scheduling、TestPlan、Evidence、Report |
| OperaMind Local Agent | ローカル Workspace と設計書原本、Git、Code Graph のローカル読取、Compile／Test／Coverage、Playwright、被テストシステム接続、SecretStore、Server Task の実行 |
| VS Code Extension | ローカル Workspace と VS Code GitHub Copilot の接続、現在 Stage の表示、同じ Confirmation API の利用、Local Agent／MCP の診断 |

Local Agent は HTTPS／WSS で Server へ外向きに接続する。利用者端末に受信 Port を公開しない。Server は利用者端末の絶対 Path を直接開かず、Agent は OperaMind Control Database へ直接接続しない。

## Embedding、Vector、RAG

Server 版では **Embedding の生成、Vector の保存、RAG 検索を Server に統一する**。Local Agent に Embedding Model の導入を要求しない。

```text
Local Agent が確認済み設計書を読み取る
  → 文書構造、許可済み Canonical 片、Revision、Digest を Server へ送る
  → Server の内部 Embedding Model が Vector を生成する
  → Organization／Project 単位で pgvector に保存する
  → Server が Requirement に対する RAG 検索を実行する
  → document_id／chunk_id／候補片／Digest を Agent へ返す
  → Agent が現在の実ファイル Revision と Digest を再検証する
  → Agent が完全な実文書を限定 Copilot Task に提供する
```

原則は次のとおりとする。

- Embedding は Server 内の管理対象 Model を使用し、既定で外部 AI API へ文書を送信しない。
- Vector、Canonical 文書片、Search Index は `organization_id` と `project_id` で隔離する。
- Vector は検索用派生物であり、正本ではない。正本は確認済み Canonical Snapshot と Agent が検証した実ファイルである。
- Vector は Embedding Model、Dimension、Profile Version、Document Revision、Content Digest と結合する。
- Revision、Digest、Model、Dimension、Profile の不一致を検出した Index は `stale` とし、検索や Copilot Context に使用しない。
- Project 削除、保持期限終了、アクセス権失効時には Canonical 片と Vector も同じ Policy で削除する。
- 原本ファイル全体を Server に保存するかどうかは Project Data Policy で明示する。黙って全文を Upload しない。
- Query、検索候補、文書片にも通常の Project 権限、暗号化、監査、保持期限を適用する。

## Local Agent に残すもの

次の情報と処理は Workspace または被テスト環境に近い Local Agent に残す。

- コード Workspace と設計書原本
- Git Repository、内部 Git 基線、実 Revision と Diff
- VS Code GitHub Copilot と MCP Companion
- Project 固有の Compile、Test、Coverage、Build Tool
- Playwright Browser Session、Screenshot の取得と脱敏処理
- 被テストシステムの Database／API／UI 接続
- Database DSN、Password、API Key、Cookie、Token を保持する OS SecretStore
- Frozen TestDataBinding の実レコード確認、Cleanup、read-after-write

Secret、Cookie、認証 Header、被テスト DB の接続文字列、未脱敏 Screenshot、任意の Raw DOM は Server、Copilot Context、ログ、通常 Evidence へ送信しない。

## ユーザーと表示制御

Server 版は少なくとも `Organization`、`User`、`Role`、`ProjectMembership`、`AgentRegistration`、`AgentProjectBinding`、`UserSession` を正式モデルとして持つ。

想定 Role は `system_admin`、`organization_admin`、`project_admin`、`developer`、`tester`、`approver`、`viewer` とする。画面はログイン User の Capability に応じて必要な Project、Menu、Task、Evidence だけを表示する。ただし前面の非表示だけを権限制御にせず、すべての API、Repository Query、Task Claim、Artifact Read、Screenshot Read、Confirmation を Server 側で再検証する。

Actor は認証済み Session から導出し、`X-OperaMind-Actor` や VS Code 設定値を本人性の根拠にしない。Agent は登録 User、Organization、Project、Device、Workspace Fingerprint に一致する Task だけを取得できる。

## Server Task 契約

Server が作る Task は少なくとも次を固定する。

- `organization_id`
- `project_id`
- `user_id`
- `agent_id`
- `workspace_fingerprint`
- `base_revision`
- `artifact_digest`
- `plan_revision`
- `run_id`

Agent 登録、Device Credential の更新、Owner Lease、Heartbeat、幂等 Claim／Result、取消、期限切れ、再試行上限、切断復旧、Server／Agent／VSIX Version 互換を実装する。別 Organization、Project、Agent、Workspace、Run の参照は fail closed とし、復旧時も未確認の操作を推測して再実行しない。

## IT テスト専用モード

Server 版の将来 Scopeには、設計書とコードを変更しない `task_kind=it_test_execution` を含める。

```text
test_basis
  → test_planning
  → test_plan_confirmation
  → test_data_execution
  → ui_validation
  → closure
```

このモードは既存の UiTestPlan、TestDataPlan、DataIdentityProvider、RunContext、Frozen TestDataBinding、Playwright、Cleanup、Evidence を再利用する。文書差分、Code Scope、コード変更、変更 Diff に対する Compile Gate は作らない。既存設計書、コード、テスト仕様、Acceptance Criteria、Release Note または利用者が確認した自然言語 Test Basis を読み取り専用で使用し、期待結果の根拠がない場合は AI が補完せず `blocked` とする。

## 配布と互換

将来は少なくとも次の配布物に分離する。

- `OperaMindServer`
- `OperaMindAgent`
- Windows 用 `OperaMindMcp.exe`
- `operamind-copilot-bridge.vsix`

WSL、Podman、Docker を必須条件にしない。完全なローカル Build 手順を用意し、Server、Agent、MCP、VSIX の Version は一つの Source から生成する。互換範囲外の組合せは起動または Task Claim 前に明示的に停止する。

現在の `desktop_local` Mode は維持し、Loopback、単機、認証なしという現行契約を壊さない。Server Mode は認証、HTTPS、Project Isolation が揃うまで有効化しない。両 Mode は Domain Artifact、Confirmation、Coverage、Evidence の実装を共有し、二つの業務フローを複製しない。

## 実装開始時の順序

1. Desktop、MCP、VSIX の Version Source を統一する。
2. Server／Agent 間の公開 Contract と Data Policy を設計・固定する。
3. User、Organization、Project Membership、Role、Session を追加する。
4. Agent 登録、Credential、Task Lease、Heartbeat、切断復旧を実装する。
5. Workspace、Command、Playwright、Target Secret を Agent 境界へ移す。
6. Embedding、pgvector、RAG を Server の Project 隔離境界へ移す。
7. `it_test_execution` を追加する。
8. 配布物、Upgrade、Rollback、Backup、監査、手動 E2E 手順を整備する。
9. Windows Native、実 PostgreSQL、実 Embedding、実 VS Code Copilot、実 Playwright の閉ループを検証する。

Fake、推測、静かな fallback を完成 Evidence に使用しない。実環境がない項目は `blocked` または未完了として残す。

