# WSL2 / Podman セットアップ

OperaMind vNext の Windows 開発環境は、Windows を VS Code のホスト、WSL2 を Python・Podman・PostgreSQL・Playwright の実行環境として分離します。リポジトリは `/mnt/c` ではなく、`~/src/OperaMind-vNext` のような WSL 側のファイルシステムに置いてください。

## 1. Windows 側の準備

管理者 PowerShell で Ubuntu 24.04 をインストールし、要求された場合は Windows を再起動します。

```powershell
wsl --install -d Ubuntu-24.04
```

初回起動時に Linux ユーザーを作成します。VS Code には Remote Development と GitHub Copilot をインストールし、`WSL: Open Folder in WSL` から WSL 内のリポジトリを開きます。

## 2. WSL 側の一括インストール

```bash
git clone <repository-url> ~/src/OperaMind-vNext
cd ~/src/OperaMind-vNext
chmod +x scripts/install-wsl.sh
./scripts/install-wsl.sh install
```

スクリプトは次を繰り返し実行可能な形で準備します。

- Python 3.12 仮想環境と開発依存関係
- rootless Podman
- `pgvector/pgvector:0.8.2-pg18-bookworm` の PostgreSQL 18
- Canonical DB と統合テスト用 DB
- 全 migration と pgvector extension
- Playwright 1.61 の Chromium、および本プロジェクトの `channel="msedge"` に必要な Linux 版 Microsoft Edge
- VSIX ビルドに必要な Node.js 22 と OperaMind Copilot Bridge VSIX
- Git 管理外の `.env.wsl`（DB Password、Bridge Token は自動生成）

Playwright は WSL 内で headless 実行します。画面表示は不要です。headed 実行を行う場合だけ WSLg が必要です。

## 3. 起動と停止

```bash
./scripts/install-wsl.sh start
```

Windows 側のブラウザーで `http://127.0.0.1:8765` を開きます。単機利用を前提とするためユーザー認証はありません。Web は `Ctrl+C` で停止し、PostgreSQL は次のコマンドで停止します。

```bash
./scripts/install-wsl.sh stop
./scripts/install-wsl.sh status
```

## 3.1 WSL + Podman + Edge Readiness Evidence

先に `./scripts/install-wsl.sh start` を別の WSL ターミナルで起動し、Podman PostgreSQL と Web を稼働させます。次に、Canonical の Project ID と Analysis Case ID を指定して、現在の WSL source tree・PostgreSQL・Microsoft Edge に結び付いた全回帰 Evidence を生成します。

```bash
bash scripts/regenerate-readiness-wsl.sh visiondemo visiondemo-expense-status-filter
```

このコマンドは macOS や native Linux では実行できません。`readiness/evidence/` と `readiness/mvp-readiness.json` の更新はコマンドが検証済みの結果だけを行い、Edge または Podman が無い場合は Evidence を生成せず終了します。

## 4. 旧環境から WSL へ移行する場合

インストールスクリプトは空の新環境を作るだけです。既存の Canonical Data と Evidence を持ち込む場合は、旧環境でまず Bundle を作成します。Bundle には PostgreSQL の Canonical DB、逐表の行数検証情報、`readiness/evidence` のみが入り、パスワード・API Key・Bridge Token は入りません。

旧環境で `pg_dump` と `psql` が使える場合は、次のように実行します。`OPERAMIND_DATABASE_URL` はコマンドラインに直書きせず、現在の環境から値を設定してください。

```bash
export OPERAMIND_DATABASE_URL='postgresql://operamind:<password>@127.0.0.1:5432/operamind'
./scripts/migrate-environment.sh export \
  --output ~/operamind-migration.tar.gz \
  --database-url "$OPERAMIND_DATABASE_URL"
```

旧環境の PostgreSQL が Podman コンテナ内にある場合は、URL の代わりにコンテナ名を指定します。

```bash
./scripts/migrate-environment.sh export \
  --output ~/operamind-migration.tar.gz \
  --source-container operamind-postgres
```

生成された `operamind-migration.tar.gz` と `.sha256` を WSL 側へコピーし、WSL のインストール後に検証してから復元します。`--replace` は WSL 側の Canonical DB を置き換える明示確認です。復元前の DB は `.operamind-backups/` に自動保存されます。

```bash
./scripts/migrate-environment.sh verify \
  --bundle ~/operamind-migration.tar.gz
./scripts/migrate-environment.sh restore \
  --bundle ~/operamind-migration.tar.gz \
  --replace
```

復元では DB の逐表行数を再計算し、Bundle の値と一致しなければ自動的に復元前バックアップへ戻します。その後 migration を実行し、`readiness/evidence/environment-restore-*.json` に結果を記録します。新しい `.env.wsl` の Web Token と Bridge Token は Bundle に含めません。復元完了時に Web Token を Web 起動環境へ、Bridge Token を Windows 側 VS Code の SecretStorage へ再登録してください。

`.env.wsl` は Shell として実行されません。空行・`#` コメント以外は厳密な `KEY=VALUE` 形式で、既知のキーだけを使用し、引用符やコマンド置換は書かないでください。未知キー、重複キー、不正な DB URL・Port・Token は起動前に拒否されます。

## 5. Windows の LM Studio を使う場合

Embedding は既存方針どおり Windows 側の LM Studio に残し、WSL のセットアップスクリプトからはインストールしません。Windows 11 22H2 以降では WSL の mirrored networking を使うと WSL と Windows の間で `127.0.0.1` を利用できます。NAT のままなら WSL から Windows host IP を取得します。

```bash
# NAT の場合
ip route show | awk '/default/ {print $3; exit}'

# WSL 側で設定する例（host IP は上の結果に置き換える）
export EMBED_API_URL='http://<windows-host-ip>:1234/v1'
export EMBED_API_KEY='lm-studio'
export EMBED_MODEL='text-embedding-nomic-embed-text-v1.5'
```

OperaMind Web / PostgreSQL / Playwright / Edge は WSL 内、LM Studio と VS Code は Windows 側という分離にします。

生成された `vscode-extension/dist/operamind-copilot-bridge.vsix` は Windows 側の VS Code から `Extensions: Install from VSIX...` でインストールします。Bridge Token は `.env.wsl` の `OPERAMIND_BRIDGE_TOKEN` と同じ値を VS Code SecretStorage に登録します。

## オプション

```bash
# 実際には変更せず、予定される処理だけを表示
./scripts/install-wsl.sh install --dry-run

# 既に OS パッケージを用意済みの場合
./scripts/install-wsl.sh install --skip-system-packages

# Playwright または VSIX を後で準備する場合
./scripts/install-wsl.sh install --skip-browser --skip-vsix
```

`.env.wsl` と `.local-tools/` は Git 対象外です。`install-wsl.sh` には DB を削除して作り直す操作を含めていません。既存 Canonical Data を誤って消さないためです。DB の置き換えは、移行 Bundle を検証したうえで `migrate-environment.sh restore --replace` を明示的に実行した場合だけ行われます。

参考：

- [Microsoft WSL のインストール](https://learn.microsoft.com/en-us/windows/wsl/install)
- [Microsoft WSL のネットワーク](https://learn.microsoft.com/en-us/windows/wsl/networking)
- [Podman の Ubuntu インストール](https://podman.io/docs/installation)
- [Playwright のブラウザーインストール](https://playwright.dev/python/docs/browsers)
- [pgvector のコンテナイメージ](https://github.com/pgvector/pgvector)
