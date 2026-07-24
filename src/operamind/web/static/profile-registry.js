"use strict";

(function exposeProfileRegistry(root, factory) {
  const api = factory();
  root.OperaMindProfileRegistry = api;
  if (typeof module === "object" && module.exports) module.exports = api;
})(typeof globalThis === "object" ? globalThis : window, function createProfileRegistryApi() {
  const PROFILE_LABELS = Object.freeze({
    DocumentConventionProfile: "文書ルール",
    DocumentRelationProfile: "文書関係ルール",
    EmbeddingProfile: "埋め込み検索",
    CodeFrameworkProfile: "コードフレームワーク",
    CommandExecutionProfile: "コマンド",
    UiLocatorProfile: "画面要素の特定ルール",
  });
  const LAYER_LABELS = Object.freeze({
    snapshot: "スナップショット",
    impact: "影響分析",
    test_plan: "テスト計画",
    evidence: "証跡",
    closure: "完了判定",
  });
  const ACTION_LABELS = Object.freeze({
    rebuild_document_snapshot: "文書スナップショットを再構築",
    rebuild_search_index: "検索索引を再構築",
    rebuild_code_graph: "コード関係図を再構築",
    review_ui_locator_profile: "画面要素の特定ルールを再レビュー",
    rerun_impact_analysis: "影響分析を再実行",
    regenerate_test_plan: "テスト計画を再生成",
    regenerate_ui_test_plan: "UI テスト計画を再生成",
    rerun_code_verification: "コード検証を再実行",
    rerun_test_data: "テストデータを再実行",
    rerun_ui_verification: "UI 検証を再実行",
    rerun_command: "コマンドを再実行",
    regenerate_change_closure: "変更完了判定を再生成",
  });
  const PHASE_LABELS = Object.freeze({
    10: "1. スナップショット",
    20: "2. 影響分析",
    30: "3. テスト計画",
    40: "4. 証跡",
    50: "5. 完了判定",
  });
  const STATUS_LABELS = Object.freeze({
    requested: "待機中",
    in_progress: "実行中",
    completed: "検証済み",
    failed: "失敗",
    blocked: "ブロック中",
  });

  function buildViewModel(value) {
    const requested = new Set(
      (value.rebuild_requests || [])
        .filter(item => ["requested", "in_progress"].includes(item.status))
        .map(item => `${item.drift_event_id}\0${item.artifact_type}\0${item.artifact_id}`),
    );
    const impacts = [];
    for (const event of value.drift_events || []) {
      for (const impact of event.impacts || []) {
        if (impact.resolved) continue;
        impacts.push(Object.freeze({
          ...impact,
          drift_event_id: event.drift_event_id,
          binding_key: event.binding_key,
          profile_change: `${event.previous_profile_version_id} → ${event.activated_profile_version_id}`,
          layer_label: LAYER_LABELS[impact.affected_layer] || impact.affected_layer,
          action_label: ACTION_LABELS[impact.rebuild_action] || impact.rebuild_action,
          requested: requested.has(`${event.drift_event_id}\0${impact.artifact_type}\0${impact.artifact_id}`),
        }));
      }
    }
    return Object.freeze({
      bindings: (value.bindings || []).map(binding => Object.freeze({
        ...binding,
        profile_label: PROFILE_LABELS[binding.profile_type] || binding.profile_type,
        candidates: (value.profile_versions || []).filter(
          version => version.profile_type === binding.profile_type,
        ),
      })),
      versions: [...(value.profile_versions || [])],
      impacts,
      rebuildBatches: (value.rebuild_batches || []).map(batch => Object.freeze({
        ...batch,
        status_label: STATUS_LABELS[batch.status] || batch.status,
      })),
      rebuildRequests: (value.rebuild_requests || []).map(request => Object.freeze({
        ...request,
        phase_label: PHASE_LABELS[request.phase_order] || `工程 ${request.phase_order}`,
        action_label: ACTION_LABELS[request.rebuild_action] || request.rebuild_action,
        status_label: STATUS_LABELS[request.status] || request.status,
        retryable: ["failed", "blocked"].includes(request.status)
          && request.attempt_count < request.max_attempts,
      })),
      openDriftCount: value.open_drift_count || 0,
      openImpactCount: value.open_impact_count || 0,
    });
  }

  function profileLabel(value) {
    return PROFILE_LABELS[value] || value;
  }

  return Object.freeze({buildViewModel, profileLabel});
});
