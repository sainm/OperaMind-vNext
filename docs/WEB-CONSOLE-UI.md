# OperaMind Web コンソール UI

## 情報設計

Web コンソールは、一つの長いページではなく次の五つのワークスペースで構成します。

| ワークスペース | 主な内容 |
|---|---|
| 変更フロー | 変更要件、文書差分、影響確認、実装編成、実行許可、VS Code 連携 |
| テスト | テストケース、テストデータ計画、実行、失敗管理、カバレッジ、変更完了判定 |
| 証跡 | 変更追跡、画面操作情報、未解決証跡、準備状況 |
| 運用 | 自動編成作業、実行担当、依存関係、ローカル環境診断 |
| 設定 | 標準設定プロファイル、適用設定、設定差異、再構築 |

ワークスペースを切り替えても各パネルの Canonical 状態は変更しません。表示だけを切り替え、既存の API と DOM ID を維持します。

変更フロー、テスト、証跡の先頭には「変更ワークベンチ」を表示します。現在の変更、待ち確認、ブロック、全体進捗、次の操作を同じ順序で示し、工程ナビゲーションから対応パネルへ移動できます。ブロックがある場合は通常の工程順よりブロック解消を優先して案内します。ワークベンチは既存の Canonical API 応答を投影するだけで、独自の完了状態を保存・推測しません。

作業を選択した場合は、定義、依存関係、担当履歴、担当期限、実行結果、履歴と人工操作を右側の詳細ドロワーに表示します。ドロワーは背景を `inert` にし、開いた直後に閉じるボタンへフォーカスを移動します。`Escape` で閉じると元の操作位置へ戻ります。

## 日本語用語

普通の利用者に内部モデル名を強制しないため、画面では次の名称を優先します。API、契約、ログ内の識別子は変更しません。

| 内部名 | 画面表示 |
|---|---|
| Task / Worker / Capability | 作業 / 実行担当 / 実行能力 |
| Lease / Result / Event | 担当期限 / 実行結果 / 履歴 |
| Approval Grant / Edit Packet | 実行許可 / 変更指示パッケージ |
| TestDataPlan / ChangeClosureResult | テストデータ計画 / 変更完了判定 |
| Evidence / Readiness | 証跡 / 準備状況 |
| Profile / Binding / Drift | 設定プロファイル / 適用設定 / 設定差異 |

`VS Code`、`GitHub Copilot`、`OperaMind` は製品名として維持します。

## SVG 関係図

作業依存関係図、コード関係図、変更追跡図は共通の SVG 表現を使用します。

- ホイールまたは `+` / `-` キー、画面上のボタンで拡大・縮小します。
- 空白領域をドラッグして移動し、`0` キーまたは「全体表示」で初期表示に戻します。
- 通常、重要経路、実ブロック、上流から伝播したブロックを色と線種で区別します。
- ノードはキーボードで選択でき、図の下に状態、参照、ファイル位置を表示します。
- コード関係図 API はプロジェクトで隔離し、最大 500 ノード / 1000 関係に制限します。ソース本文と内容ハッシュは Web へ返しません。

## ビジュアルシステム

色、境界線、角丸、影、フォーカス、余白は `app.css` の Design Token を使用します。

- 文字: `--font-*` と `--text-*`
- 余白: `--space-1` ～ `--space-10`
- 色: `--ink`、`--muted`、`--green`、`--amber`、`--red`、`--blue`
- 形状と階層: `--radius-*`、`--shadow-*`、`--control-height`

- 緑: 正常、完了、現在の選択
- 黄: 待機、確認、注意
- 赤: 失敗、ブロック、未解決
- 青: 情報、重要経路、照合待ち
- 灰色: 未実行、空状態、補助情報

状態は色だけで表現せず、日本語ラベル、境界線、アイコン領域を併用します。Loading はページ上端の Progress 表示と `aria-busy`、通知は閉じる操作を持つ `status` または `alert`、空状態は破線カードで区別します。停止、取消、版の取り消しは赤い危険操作として区別し、実行直前に影響を説明する再確認を行います。

Test Case、TestDataPlan のフロー、ChangeClosureResult は初期状態で開いた折りたたみグループとして表示します。利用者は概要を失わずに長い手順、変数、Assertion、Cleanup、Coverage とブロック理由を必要な単位で閉じられます。実行許可の発行など長いフォームの確定操作は、デスクトップでは画面下部に追従する操作領域に置きます。

## レスポンシブとアクセシビリティ

- 1200px 以下では運用フィルタを二列にして、1024px のノート PC でも横方向へはみ出さないようにします。
- 1000px 以下ではメインナビゲーションをオフキャンバス表示にします。
- 700px 以下ではフォーム、操作ボタン、サマリーを一列にします。
- 閉じたモバイルナビゲーションは `inert` と `aria-hidden` を設定します。
- Skip Link、ランドマーク、見出し階層、`aria-current`、`aria-expanded` を維持します。
- すべての主要操作をキーボードで実行でき、`prefers-reduced-motion` と Forced Colors に対応します。
- 390px、768px、1024px、1366px、1440px 幅で、五つのワークスペースすべてに横方向の Overflow を許可しません。

## Playwright ビジュアル回帰

既定ブラウザは Microsoft Edge です。

```bash
OPERAMIND_PLAYWRIGHT_LIVE=1 \
  .venv/bin/python -m pytest -q tests/integration/test_web_visual_regression.py
```

Edge がない開発環境では Chromium 系ブラウザの Channel を明示できます。

```bash
OPERAMIND_PLAYWRIGHT_LIVE=1 \
OPERAMIND_PLAYWRIGHT_CHANNEL=chrome \
  .venv/bin/python -m pytest -q tests/integration/test_web_visual_regression.py
```

意図した UI 変更をレビューした後だけ、次のコマンドで基準画像を更新します。

```bash
OPERAMIND_PLAYWRIGHT_LIVE=1 \
OPERAMIND_PLAYWRIGHT_CHANNEL=chrome \
OPERAMIND_UPDATE_VISUAL_BASELINE=1 \
  .venv/bin/python -m pytest -q tests/integration/test_web_visual_regression.py
```

基準画像は `tests/web/snapshots/` に保存します。変更フロー、テスト、証跡、運用、設定のデスクトップ画面と、モバイルナビゲーションを対象にします。テストは画像を復号し、24 を超える RGB 差分が全 Pixel の 1% を超えた場合に失敗します。
