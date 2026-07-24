"use strict";

(function exposeChangeManagement(root, factory) {
  const api = factory();
  root.OperaMindChangeManagement = api;
  if (typeof module === "object" && module.exports) module.exports = api;
})(typeof globalThis === "object" ? globalThis : window, function createChangeManagementApi() {
  const ACTION_LABELS = Object.freeze({
    modify: "変更",
    review_only: "レビューのみ",
    add: "追加",
    delete: "削除",
  });

  const DATA_ACTION_LABELS = Object.freeze({
    "load-default-seed": "既定データをロード",
    "create-returned-expense": "差戻し経費を登録",
  });

  const SCENARIO_LABELS = Object.freeze({
    "expense-filter-default-all": "経費一覧の初期表示ですべてを表示",
    "expense-filter-returned-option": "差戻しを選択して対象経費を絞り込み",
    "expense-filter-reset-all": "リセット後にすべての経費を再表示",
  });

  const SCENARIO_EXPECTATIONS = Object.freeze({
    "expense-filter-default-all": "登録済み経費をすべて表示する",
    "expense-filter-returned-option": "差戻し経費だけを表示する",
    "expense-filter-reset-all": "すべての経費を再表示する",
  });

  function actionLabel(action) {
    return ACTION_LABELS[action] || action;
  }

  function dataActionLabel(action) {
    return DATA_ACTION_LABELS[action.step_id] || `データ生成ステップ ${action.sequence}`;
  }

  function scenarioLabel(item) {
    return SCENARIO_LABELS[item.scenario_id] || `UI シナリオ ${item.scenario_id}`;
  }

  function scenarioExpected(item) {
    return SCENARIO_EXPECTATIONS[item.scenario_id] || (item.expected_results || []).join(" / ");
  }

  function orchestrationCards(bundle) {
    const plan = bundle.orchestration;
    const data = bundle.test_data_plan;
    const coverage = bundle.coverage_report;
    return [
      {
        title: "コード変更範囲",
        items: (plan.code_scope || []).map(
          item => `${actionLabel(item.recommended_action)} · ${item.target_path}`,
        ),
      },
      {
        title: "テストデータ生成フロー（画面横断対応）",
        items: (data.generation_flows || []).map(
          flow => `データセット ${(flow.test_data_refs || []).join("、")} を生成\n${
            (flow.steps || []).map(step => `${step.sequence}. ${dataActionLabel(step)}`).join(" → ")
          }`,
        ),
      },
      {
        title: "UI シナリオ",
        items: (plan.ui_scenarios || []).map(
          item => `${scenarioLabel(item)}\n期待結果: ${scenarioExpected(item)}`,
        ),
      },
      {
        title: "業務カバレッジ",
        items: [
          `${coverage.covered_rule_count} / ${coverage.business_rule_count} ルール (${coverage.coverage_percent}%)`,
        ],
      },
    ];
  }

  return Object.freeze({
    actionLabel,
    dataActionLabel,
    orchestrationCards,
    scenarioExpected,
    scenarioLabel,
  });
});
