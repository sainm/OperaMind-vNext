"use strict";

(function exposeTestDataManagement(root, factory) {
  const api = factory();
  root.OperaMindTestDataManagement = api;
  if (typeof module === "object" && module.exports) module.exports = api;
})(typeof globalThis === "object" ? globalThis : window, function createTestDataManagementApi() {
  const CHANNEL_LABELS = Object.freeze({
    fixture: "固定データ",
    http: "API",
    sql: "SQL",
    ui: "画面操作",
    api: "API",
    database: "データベース",
    test: "テスト",
    response: "応答",
    request: "要求",
    assertion: "検証条件",
  });
  const OPERATOR_LABELS = Object.freeze({
    equals: "＝",
    not_equals: "≠",
    contains: "を含む",
    exists: "が存在する",
    matches: "に一致",
    satisfies: "を満たす",
    greater_than: "より大きい",
    less_than: "より小さい",
  });
  const SCREEN_LABELS = Object.freeze({"employee-list": "社員一覧", "expense-list": "経費一覧"});
  const UI_ACTION_LABELS = Object.freeze({
    "search-created-employee": "作成した社員を検索",
    "search-created-expense": "作成した経費を検索",
  });

  function channelLabel(channel) {
    return CHANNEL_LABELS[channel] || channel;
  }

  function operatorLabel(operator) {
    return OPERATOR_LABELS[operator] || operator;
  }

  function screenLabel(value) {
    return SCREEN_LABELS[value] || value;
  }

  function uiActionLabel(value) {
    return UI_ACTION_LABELS[value] || value;
  }

  function stepTargetLabel(step) {
    if (step.channel === "ui") return `${screenLabel(step.screen_ref)} / ${uiActionLabel(step.ui_action_ref)}`;
    return step.target || "対象未指定";
  }

  function inputVariableNames(value, found = new Set()) {
    if (typeof value === "string") {
      for (const match of value.matchAll(/\{\{([A-Za-z_][A-Za-z0-9_]*)\}\}/g)) found.add(match[1]);
    } else if (Array.isArray(value)) {
      for (const item of value) inputVariableNames(item, found);
    } else if (value && typeof value === "object") {
      for (const item of Object.values(value)) inputVariableNames(item, found);
    }
    return [...found];
  }

  return Object.freeze({
    channelLabel,
    inputVariableNames,
    operatorLabel,
    screenLabel,
    stepTargetLabel,
    uiActionLabel,
  });
});
