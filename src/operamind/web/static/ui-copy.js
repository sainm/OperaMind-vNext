"use strict";

(function exposeUiCopy(root, factory) {
  const api = factory();
  root.OperaMindUiCopy = api;
  if (typeof module === "object" && module.exports) module.exports = api;
})(typeof globalThis === "object" ? globalThis : window, function createUiCopyApi() {
  const STATUS_LABELS = Object.freeze({
    ready: "準備完了", claimed: "担当中", submitted: "結果提出済み・照合待ち",
    released: "解放済み", passed: "合格", pending: "待機中", requested: "受付済み",
    pending_confirmation: "VS Code 確認待ち", accepted: "VS Code で確認済み",
    in_progress: "実行中", waiting: "確認・外部処理待ち", running: "実行中",
    not_run: "未実行", not_required: "不要", missing: "証跡不足",
    not_impacted: "影響なし", confirmed: "確認済み", complete: "完了",
    truncated: "一部表示", completed: "完了", valid: "有効",
    active: "有効", active_editing: "編集中・承認有効", deterministic: "差分確認待ち",
    ready_for_confirmation: "全体差分の確認待ち", needs_confirmation: "選択確認が必要",
    applied: "適用済み", current: "現行版", undone: "取り消し済み",
    cancelled: "取消済み", superseded: "旧版", failed: "失敗", blocked: "ブロック",
    interrupted: "中断", reanalysis_required: "再分析が必要", expired: "期限切れ",
    revision_requested: "修正依頼済み", awaiting_confirmation: "確認待ち",
    editing: "編集中", modified: "変更", added: "追加", deleted: "削除",
    covered: "カバー済み", uncovered: "未カバー", high: "高", medium: "中", low: "低",
    mvp_ready: "MVP 準備完了", golden_ready_partial: "一部準備完了",
    partial_ready: "一部準備完了", dev_silver: "開発中", stale: "再検証が必要",
    changed: "変更あり", unchanged: "変更なし", reusable: "実行許可を再利用可能",
    reused: "実行許可を再利用済み", reconfirmed: "再確認済み",
    confirmation_required: "再確認が必要", grant_required: "実行許可が必要",
    original: "初版の承認", draft: "レビュー待ち", approved: "承認済み",
    rejected: "却下", partial: "一部観測", unique_visible: "一意かつ表示",
    not_found: "未検出", hidden: "非表示", ambiguous: "複数一致",
    navigation_failed: "画面遷移失敗", clear: "未解決なし",
    needs_evidence: "証跡が必要", open: "未解決", closed: "解決済み",
    defined: "定義済み", unknown: "不明", unresolved: "未解決",
    planned: "計画済み", prepared: "準備済み", succeeded: "成功",
    in_scope: "承認範囲内", auto_confirmed: "自動確認済み",
    needs_review: "レビューが必要", reviewed: "レビュー済み", frozen: "凍結済み",
    needs_reanalysis: "再分析が必要", ready_for_copilot_review: "Copilot 確認待ち",
    draft_created: "下書き作成済み", candidate_materialized: "候補生成済み",
    attention_required: "確認が必要", revoked: "失効済み", wrong: "不一致",
    first: "初回観測", online: "オンライン", draining: "新規受付終了中",
    offline: "停止", warning: "注意", ok: "正常",
  });

  const TERMS = Object.freeze({
    task: "作業", worker: "実行担当", capability: "実行能力", lease: "担当期限",
    result: "実行結果", event: "履歴", run: "実行", readiness: "準備状況",
    evidence: "証跡", profile: "設定プロファイル", drift: "設定差異",
    codeGraph: "コード関係図", taskGraph: "作業依存関係図",
    traceabilityGraph: "変更追跡図", testCase: "テストケース",
    closureResult: "変更完了判定", approvalGrant: "実行許可",
    editPacket: "変更指示パッケージ",
  });

  function statusLabel(value) {
    return STATUS_LABELS[value] || value || "不明";
  }

  function term(value) {
    return TERMS[value] || value;
  }

  return Object.freeze({statusLabel, term, STATUS_LABELS, TERMS});
});
