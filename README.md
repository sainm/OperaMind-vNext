# OperaMind vNext

OperaMind vNext は、設計書の変更を起点としてコードへの影響範囲を特定し、VS Code GitHub Copilot によるコード変更を制御したうえで、OperaMind が対象システムの UI 検証を実行するための設計ベースラインプロジェクトです。

現在のリポジトリには、アーキテクチャ、コアデータ契約、Profile のサンプル、Golden Dataset、および P0 データベース基盤と検証コードが含まれています。旧 OperaMind の Java、Python、React、生成スクリプトは継承していません。

## メインフロー

![OperaMind vNext のメインフロー](docs/assets/operamind-main-flow.svg)

## 全体アーキテクチャ

![OperaMind vNext の全体アーキテクチャ](docs/assets/operamind-overall-architecture.png)

## 不変の原則

- PostgreSQL は、ドキュメント、バージョン、ノード、リレーション、コードグラフ、影響分析結果、検証結果などの Canonical Data を保存します。
- pgvector は再構築可能な Search Index であり、`section_id` / `chunk_id` の候補のみを返します。
- 正式な影響分析では、Snapshot 単位で実データによる RAG を必ず実行します。keyword-only や fixture から確認可能なレポートを生成することはできません。
- Context Package は業務分析への入力です。Copilot による変更フェーズでは、承認済みの Edit Packet とローカルコードのみを参照します。
- Copilot が変更できるのは許可リスト内のファイルだけです。範囲を超える場合は処理を停止し、再分析しなければなりません。
- OperaMind は、影響を受ける UI シナリオ、Playwright の実行、ブラウザ上のエビデンス、および最終クローズを担当します。

## リポジトリ構成

```text
docs/             アーキテクチャ、MVP、RAG、Code Graph、Copilot、UI 検証の設計
contracts/        8 つのコア Artifact JSON Schema
profiles/         Embedding、設計書の記述パターン、コードフレームワークの Profile サンプル
golden-dataset/   人手で確認されたエンドツーエンドの正解データテンプレート
decisions/        Greenfield の境界と旧プロジェクトから抽出した知見の記録
```

## 実装の進め方

1. `docs/MVP-SCOPE.md` を読み、第一段階のスコープを確認します。
2. 2 つの実プロジェクトを使用して Golden Dataset を構築します。
3. `contracts/` 内の v1 契約を確定します。
4. `docs/IMPLEMENTATION-ROADMAP.md` に従って実行可能なコードを実装します。

P0 ベースラインの検証は次のコマンドで実行します。

```bash
operamind-baseline
operamind-baseline --require-ready
operamind-migrate
```

セットアップ、migration、手動確認項目、既知の制限については、`docs/P0-BASELINE.md` を参照してください。

## ステータス

`P0-in-progress`：契約および Golden Dataset の検証入口と初回 PostgreSQL migration を実装しています。実行可能なサービスはまだなく、データベース、Embedding Provider、GitHub Copilot、Playwright との連携が完了しているとみなすことはできません。
