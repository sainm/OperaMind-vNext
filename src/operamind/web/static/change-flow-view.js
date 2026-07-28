(function(root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  root.OperaMindChangeFlow = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function() {
  "use strict";

  const statusLabels = {
    completed: "完了",
    running: "実行中",
    waiting: "待機中",
    blocked: "停止",
    not_required: "対象外",
    in_progress: "進行中",
    ready: "準備完了",
    passed: "合格",
    failed: "失敗",
    pending: "待機中",
    not_run: "未実行"
  };

  const executorLabels = {
    user: "利用者",
    vscode_github_copilot: "VS Code GitHub Copilot",
    operamind: "OperaMind"
  };

  const publicStageIds = new Set([
    "requirement",
    "document_change",
    "code_scope",
    "compile_test",
    "ui_validation",
    "final_report"
  ]);

  const detailLabels = {
    requirement_text: "変更要件",
    business_rules: "業務ルール",
    change_count: "差分件数",
    changes: "設計書差分",
    base_revision: "基準リビジョン",
    impact_status: "影響解析状態",
    ui_impact_status: "UI 影響",
    items: "変更対象コード",
    copilot_task_state: "Copilot 状態",
    edit_status: "コード変更状態",
    test_plan_status: "テスト計画",
    test_cases: "テストケース",
    result_revision: "結果リビジョン",
    command_evidence_status: "コンパイル・テスト",
    commands: "実行結果",
    test_data_plan_status: "テストデータ計画",
    test_data_status: "テストデータ",
    ui_status: "UI 検証",
    cleanup_status: "クリーンアップ",
    generation_flows: "データ生成・UI 手順",
    screenshots: "スクリーンショット",
    closure_status: "完了判定",
    business_coverage_percent: "業務カバレッジ",
    changed_line_coverage_percent: "変更行カバレッジ",
    modified_paths: "変更ファイル",
    test_results: "テスト結果",
    unresolved_items: "未解決項目"
  };

  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  function stageRail(stages, currentStage) {
    return stages.filter(stage => publicStageIds.has(stage.stage_id)).map((stage, index) => `
      <button class="stage-step ${escapeHtml(stage.status)} ${stage.stage_id === currentStage ? "current" : ""}"
        type="button" data-stage-target="${escapeHtml(stage.stage_id)}">
        <span class="stage-index">${index + 1}</span>
        <span><strong>${escapeHtml(stage.label)}</strong><small>${escapeHtml(statusLabels[stage.status] || stage.status)}</small></span>
      </button>`).join("");
  }

  function stageDetails(stages) {
    return stages.filter(stage => publicStageIds.has(stage.stage_id)).map((stage, index) => {
      const blockers = (stage.blocking_reasons || []).map(
        reason => `<li>${escapeHtml(reason)}</li>`
      ).join("");
      return `
        <article id="stage-${escapeHtml(stage.stage_id)}" class="stage-card ${escapeHtml(stage.status)}">
          <header>
            <span class="stage-card-number">${String(index + 1).padStart(2, "0")}</span>
            <div>
              <div class="stage-title-line">
                <h3>${escapeHtml(stage.label)}</h3>
                <span class="status-badge ${escapeHtml(stage.status)}">${escapeHtml(statusLabels[stage.status] || stage.status)}</span>
              </div>
              <p>${escapeHtml(stage.summary)}</p>
            </div>
            <span class="executor">${escapeHtml(executorLabels[stage.executor] || stage.executor)}</span>
          </header>
          ${blockers ? `<div class="blocker-box"><strong>停止理由</strong><ul>${blockers}</ul></div>` : ""}
          <div class="detail-grid">${renderDetails(stage.details || {})}</div>
        </article>`;
    }).join("");
  }

  function renderDetails(details) {
    return Object.entries(details)
      .filter(([key, value]) => Object.prototype.hasOwnProperty.call(detailLabels, key)
        && value !== null
        && value !== undefined
        && value !== ""
        && !(Array.isArray(value) && value.length === 0))
      .map(([key, value]) => `
        <div class="detail-item ${Array.isArray(value) ? "wide" : ""}">
          <dt>${escapeHtml(detailLabels[key] || key)}</dt>
          <dd>${renderValue(key, value)}</dd>
        </div>`)
      .join("") || '<p class="empty-detail">成果物はまだありません。</p>';
  }

  function renderValue(key, value) {
    if (key.endsWith("_percent") && typeof value === "number") {
      return `<strong class="metric">${escapeHtml(value)}%</strong>`;
    }
    if (key === "screenshots" && Array.isArray(value)) {
      return `<div class="screenshot-grid">${value.map(item => item.available && item.content_url
        ? `<a href="${escapeHtml(item.content_url)}" target="_blank" rel="noreferrer"><img src="${escapeHtml(item.content_url)}" alt="UI 検証スクリーンショット"></a>`
        : '<span class="evidence-chip">スクリーンショット未取得</span>').join("")}</div>`;
    }
    if (key === "changes" && Array.isArray(value)) {
      return `<ul class="value-list">${value.map(item => `<li>${renderDocumentChange(item)}</li>`).join("")}</ul>`;
    }
    if (key === "items" && Array.isArray(value)) {
      return `<ul class="value-list scope-list">${value.map(item => `<li>${renderScopeItem(item)}</li>`).join("")}</ul>`;
    }
    if (key === "commands" && Array.isArray(value)) {
      return `<ul class="value-list command-list">${value.map(item => `<li>${renderCommand(item)}</li>`).join("")}</ul>`;
    }
    if (key === "test_cases" && Array.isArray(value)) {
      return `<div class="plan-list">${value.map(renderTestCase).join("")}</div>`;
    }
    if (key === "generation_flows" && Array.isArray(value)) {
      return `<div class="plan-list">${value.map(renderGenerationFlow).join("")}</div>`;
    }
    if (key === "test_results" && Array.isArray(value)) {
      return `<ul class="value-list">${value.map(item => `<li>${renderTestResult(item)}</li>`).join("")}</ul>`;
    }
    if (Array.isArray(value)) {
      return `<ul class="value-list">${value.map(item => `<li>${renderListItem(item)}</li>`).join("")}</ul>`;
    }
    if (typeof value === "object") return `<pre>${escapeHtml(JSON.stringify(value, null, 2))}</pre>`;
    return escapeHtml(value);
  }

  function renderListItem(item) {
    if (typeof item !== "object" || item === null) return escapeHtml(item);
    const title = item.text || item.summary || item.change_id || item.business_rule_id || item.evidence_id;
    const deltaCount = Array.isArray(item.field_deltas) ? item.field_deltas.length : 0;
    return `<strong>${escapeHtml(title || "項目")}</strong>${deltaCount ? `<small>変更フィールド ${deltaCount} 件</small>` : ""}`;
  }

  function renderDocumentChange(item) {
    if (typeof item !== "object" || item === null) return escapeHtml(item);
    const heading = item.summary || [item.domain, item.fact_type].filter(Boolean).join(" / ") || "設計書変更";
    const meta = [item.change_type, item.domain, item.fact_type].filter(Boolean);
    const deltas = Array.isArray(item.field_deltas) ? item.field_deltas : [];
    const deltaHtml = deltas.length ? `<dl class="field-deltas">${deltas.map(delta => `
      <div>
        <dt>${escapeHtml(delta.field || "項目")}</dt>
        <dd><del>${formatInlineValue(delta.before)}</del><span aria-hidden="true">→</span><ins>${formatInlineValue(delta.after)}</ins></dd>
      </div>`).join("")}</dl>` : "";
    return `<strong>${escapeHtml(heading)}</strong>
      ${meta.length ? `<small>${meta.map(escapeHtml).join(" · ")}</small>` : ""}
      ${deltaHtml}`;
  }

  function renderScopeItem(item) {
    if (typeof item !== "object" || item === null) return escapeHtml(item);
    const symbols = Array.isArray(item.target_symbols) ? item.target_symbols : [];
    const tests = Array.isArray(item.test_file_refs) ? item.test_file_refs : [];
    return `<div class="list-heading">
        <strong>${escapeHtml(item.target_path || "対象ファイル")}</strong>
        ${item.recommended_action ? `<span class="action-chip">${escapeHtml(item.recommended_action)}</span>` : ""}
      </div>
      ${symbols.length ? `<small>対象シンボル: ${symbols.map(escapeHtml).join(", ")}</small>` : ""}
      ${item.rationale ? `<p>${escapeHtml(item.rationale)}</p>` : ""}
      ${tests.length ? `<small>関連テスト: ${tests.map(escapeHtml).join(", ")}</small>` : ""}`;
  }

  function renderCommand(item) {
    if (typeof item !== "object" || item === null) return escapeHtml(item);
    const status = item.status || "pending";
    const exit = item.exit_code === null || item.exit_code === undefined ? "" : ` / exit ${item.exit_code}`;
    return `<div class="list-heading">
        <strong>${escapeHtml(item.command_ref || "コマンド")}</strong>
        <span class="status-badge ${escapeHtml(status)}">${escapeHtml(statusLabels[status] || status)}</span>
      </div>
      <small>${escapeHtml(`${status}${exit}`)}</small>`;
  }

  function renderTestCase(item) {
    if (typeof item !== "object" || item === null) return escapeHtml(item);
    return `<section class="plan-card">
      <div class="list-heading">
        <strong>${escapeHtml(item.title || "テストケース")}</strong>
        <span class="plan-meta">${[item.level, item.execution_mode].filter(Boolean).map(escapeHtml).join(" · ")}</span>
      </div>
      ${renderTextSteps("事前条件", item.preconditions)}
      ${renderTextSteps("手順", item.steps, true)}
      ${renderTextSteps("期待結果", item.expected_results)}
    </section>`;
  }

  function renderGenerationFlow(item) {
    if (typeof item !== "object" || item === null) return escapeHtml(item);
    const status = item.status || "pending";
    return `<section class="plan-card">
      <div class="list-heading">
        <strong>${escapeHtml(item.title || "データ生成フロー")}</strong>
        <span class="status-badge ${escapeHtml(status)}">${escapeHtml(statusLabels[status] || status)}</span>
      </div>
      <div class="flow-step-list">${(item.steps || []).map(renderGenerationStep).join("")}</div>
      ${renderAssertions("最終断言", item.final_assertions)}
      <div class="cleanup-panel">
        <strong>クリーンアップ</strong>
        <small>${escapeHtml(item.cleanup_policy || "未設定")}</small>
        <div class="flow-step-list">${(item.cleanup_steps || []).map(renderGenerationStep).join("")}</div>
      </div>
    </section>`;
  }

  function renderGenerationStep(item) {
    if (typeof item !== "object" || item === null) return escapeHtml(item);
    const status = item.status || "pending";
    const inputs = Array.isArray(item.input_variables) ? item.input_variables : [];
    const outputs = Array.isArray(item.output_variables) ? item.output_variables : [];
    return `<article class="flow-step">
      <div class="flow-step-heading">
        <span class="step-sequence">${escapeHtml(item.sequence || "–")}</span>
        <div><strong>${escapeHtml(item.business_action || "処理")}</strong><small>${escapeHtml(item.channel || "")}</small></div>
        <span class="status-badge ${escapeHtml(status)}">${escapeHtml(statusLabels[status] || status)}</span>
      </div>
      ${inputs.length ? `<p><b>入力変数:</b> ${inputs.map(escapeHtml).join(", ")}</p>` : ""}
      ${outputs.length ? `<p><b>出力変数:</b> ${outputs.map(escapeHtml).join(", ")}</p>` : ""}
      ${renderAssertions("事後断言", item.assertions)}
      ${item.failure_reason ? `<p class="step-failure">${escapeHtml(item.failure_reason)}</p>` : ""}
    </article>`;
  }

  function renderAssertions(label, assertions) {
    if (!Array.isArray(assertions) || assertions.length === 0) return "";
    return `<div class="assertion-list"><strong>${escapeHtml(label)}</strong><ul>${assertions.map(assertion => `
      <li><span>${escapeHtml(assertion.observe_via || "確認")}</span>
      ${escapeHtml(assertion.subject || "対象")} ${escapeHtml(assertion.operator || "")}
      <b>${formatInlineValue(assertion.expected)}</b></li>`).join("")}</ul></div>`;
  }

  function renderTextSteps(label, values, ordered = false) {
    if (!Array.isArray(values) || values.length === 0) return "";
    const tag = ordered ? "ol" : "ul";
    return `<div class="test-copy"><strong>${escapeHtml(label)}</strong><${tag}>${values.map(value => `<li>${escapeHtml(value)}</li>`).join("")}</${tag}></div>`;
  }

  function renderTestResult(item) {
    if (typeof item !== "object" || item === null) return escapeHtml(item);
    const status = item.status || "pending";
    return `<div class="list-heading">
        <strong>${escapeHtml(item.title || "テストケース")}</strong>
        <span class="status-badge ${escapeHtml(status)}">${escapeHtml(statusLabels[status] || status)}</span>
      </div>
      ${item.summary ? `<p>${escapeHtml(item.summary)}</p>` : ""}`;
  }

  function formatInlineValue(value) {
    if (value === null || value === undefined || value === "") return '<span class="empty-value">なし</span>';
    if (typeof value === "object") return escapeHtml(JSON.stringify(value));
    return escapeHtml(value);
  }

  return {escapeHtml, stageRail, stageDetails, statusLabels, executorLabels};
});
