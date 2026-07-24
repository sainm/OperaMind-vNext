"use strict";

(function exposeCaseEditor(root, factory) {
  const api = factory();
  root.OperaMindCaseEditor = api;
  if (typeof module === "object" && module.exports) module.exports = api;
})(typeof globalThis === "object" ? globalThis : window, function createCaseEditorApi() {
  const FIELD_LABELS = Object.freeze({
    steps: "テスト手順",
    test_data_refs: "テストデータ",
    test_data_values: "テストデータ項目",
    expected_results: "期待結果",
    business_assertions: "業務アサーション",
  });

  function fieldLabel(field) {
    return FIELD_LABELS[field] || field;
  }

  function normalizeProposal(proposal) {
    if (!proposal) return null;
    return {
      ...proposal,
      operations: (proposal.operations || []).map(operation => ({
        ...operation,
        field_label: fieldLabel(operation.field),
      })),
      ambiguities: (proposal.ambiguities || []).map(ambiguity => ({
        ...ambiguity,
        options: [...(ambiguity.options || [])],
      })),
    };
  }

  function selectedAmbiguities(proposal, selections) {
    const missing = [];
    const selected = {};
    for (const ambiguity of (proposal && proposal.ambiguities) || []) {
      const value = selections[ambiguity.ambiguity_id];
      if (!value) missing.push(ambiguity.ambiguity_id);
      else selected[ambiguity.ambiguity_id] = value;
    }
    return Object.freeze({missing, selected});
  }

  return Object.freeze({fieldLabel, normalizeProposal, selectedAmbiguities});
});
