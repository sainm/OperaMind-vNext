"use strict";

(function exposeVerificationResults(root, factory) {
  const api = factory();
  root.OperaMindVerificationResults = api;
  if (typeof module === "object" && module.exports) module.exports = api;
})(typeof globalThis === "object" ? globalThis : window, function createVerificationResultsApi() {
  const BLOCKING_LABELS = Object.freeze({
    "Committed Edit Result is missing": "コミット済みの変更実行結果がありません。",
    "Edit Result is not committed": "変更実行結果がコミット済みではありません。",
    "Edit Result is not in scope": "変更実行結果が承認範囲外です。",
    "Edit Result command evidence is not verified": "変更実行結果のコマンド証跡が検証されていません。",
    "Changed-line coverage evidence is missing": "変更行カバレッジの証跡がありません。",
    "Legacy ChangeClosureResult v1 requires changed-line coverage re-evaluation": "旧形式の変更完了判定には変更行カバレッジがないため、再評価が必要です。",
    "Changed-line coverage is below threshold": "変更行カバレッジが基準値を下回っています。",
    "Business Coverage Report summary is inconsistent": "業務カバレッジの集計値と明細が一致していません。",
    "Business Coverage Report has no covered business rules": "カバー済みの業務ルールがありません。",
    "Change Closure is stale for current Edit Result": "現在の変更実行結果に対応する変更完了判定を再生成してください。",
    "Test Data Execution Result is missing": "テストデータ実行結果がありません。",
    "Test data execution result is missing": "テストデータ実行結果がありません。",
    "UI verification result is missing": "UI 検証結果がありません。",
    "UI verification is blocked or missing": "UI 検証が未実行またはブロックされています。",
    "Test data cleanup failed": "テストデータのクリーンアップに失敗しました。",
    "TestDataPlan is missing": "テストデータ計画がありません。",
    "No active Approval Grant permits TestDataPlan execution": "テストデータ計画の実行を許可する有効な実行許可がありません。",
    "Test Case execution scope requires confirmation": "改訂後のテストケース実行範囲を再確認してください。",
  });

  function blockingReasonLabel(reason) {
    if (BLOCKING_LABELS[reason]) return BLOCKING_LABELS[reason];
    let match = reason.match(/^Changed-line coverage: ([0-9.]+)% < ([0-9.]+)%$/);
    if (match) return `変更行カバレッジ ${match[1]}% は基準値 ${match[2]}% 未満です。`;
    match = reason.match(/^Uncovered changed line: (.+):(\d+)$/);
    if (match) return `未カバーの変更行があります：${match[1]}:${match[2]}`;
    match = reason.match(/^Coverage evidence does not include changed source file: (.+)$/);
    if (match) return `変更されたソースファイルのカバレッジ証跡がありません：${match[1]}`;
    match = reason.match(/^Test case (.+) is (blocked|failed|interrupted)$/);
    if (match) return `テストケース ${match[1]} は${match[2] === "failed" ? "失敗" : match[2] === "interrupted" ? "中断" : "ブロック"}しています。`;
    match = reason.match(/^(.*): Fixture target has no approved binding: (.+)$/);
    if (match) return `${match[1]}：固定データ対象 ${match[2]} の承認済みの関連付けがありません。`;
    match = reason.match(/^Uncovered business rule: (.+)$/);
    if (match) return `業務ルール ${match[1]} がテストでカバーされていません。`;
    match = reason.match(/^Out-of-scope file: (.+)$/);
    if (match) return `承認範囲外の変更ファイルがあります：${match[1]}`;
    match = reason.match(/^Unresolved impact item: (.+)$/);
    if (match) return `未解決の影響項目があります：${match[1]}`;
    match = reason.match(/^UI out-of-scope file: (.+)$/);
    if (match) return `UI 検証で範囲外の変更ファイルが見つかりました：${match[1]}`;
    return reason;
  }

  function coverageView(coverage, changeCoverage) {
    const businessPercent = coverage ? clampPercent(coverage.coverage_percent) : null;
    const changedLinePercent = changeCoverage && changeCoverage.status !== "not_required"
      ? clampPercent(changeCoverage.coverage_percent)
      : null;
    return Object.freeze({
      business: coverage ? {
        percent: businessPercent,
        covered: coverage.covered_rule_count,
        total: coverage.business_rule_count,
        items: [...(coverage.items || [])],
        status: coverage.status,
      } : null,
      changedLines: changeCoverage ? {
        percent: changedLinePercent,
        covered: changeCoverage.covered_changed_line_count || 0,
        total: changeCoverage.changed_line_count || 0,
        threshold: changeCoverage.minimum_coverage_percent,
        status: changeCoverage.status,
        files: [...(changeCoverage.files || [])],
        evidenceRefs: [...(changeCoverage.evidence_refs || [])],
        blockingReasons: (changeCoverage.blocking_reasons || []).map(reason => ({
          raw: reason,
          label: blockingReasonLabel(reason),
        })),
      } : null,
    });
  }

  function closureView(closure) {
    if (!closure) return null;
    return Object.freeze({
      status: closure.status,
      uiStatus: closure.ui_status,
      businessCoveragePercent: closure.business_coverage_percent,
      changedLineCoveragePercent: closure.changed_line_coverage_percent,
      modifiedPathCount: (closure.modified_paths || []).length,
      tests: [...(closure.test_results || [])],
      blockers: (closure.unresolved_items || []).map(reason => ({
        raw: reason,
        label: blockingReasonLabel(reason),
      })),
    });
  }

  function clampPercent(value) {
    return Math.max(0, Math.min(100, Number(value) || 0));
  }

  return Object.freeze({blockingReasonLabel, closureView, coverageView});
});
