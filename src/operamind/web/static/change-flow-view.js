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
    rag_documents: "RAG 対象設計書",
    base_revision: "基準リビジョン",
    impact_status: "影響解析状態",
    ui_impact_status: "UI 影響",
    impact_graph: "影響ファイルグラフ",
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
          ${renderConfirmation(stage.details && stage.details.confirmation)}
          <div class="detail-grid">${renderDetails(stage.details || {})}</div>
        </article>`;
    }).join("");
  }

  function renderConfirmation(confirmation) {
    if (!confirmation || !confirmation.checkpoint) return "";
    return `<section class="confirmation-panel" data-confirmation-panel>
      <div><strong>${escapeHtml(confirmation.stage_label || "人工確認")}</strong>
      <p>${escapeHtml(confirmation.message || "内容を確認してください。")}</p></div>
      <div class="confirmation-actions">
        <button type="button" class="primary" data-confirm-checkpoint="${escapeHtml(confirmation.checkpoint)}" data-subject-digest="${escapeHtml(confirmation.subject_digest)}">確認して進む</button>
        <button type="button" class="secondary" data-reject-checkpoint="${escapeHtml(confirmation.checkpoint)}" data-subject-digest="${escapeHtml(confirmation.subject_digest)}">差し戻す</button>
      </div>
    </section>`;
  }

  function renderDetails(details) {
    return Object.entries(details)
      .filter(([key, value]) => Object.prototype.hasOwnProperty.call(detailLabels, key)
        && value !== null
        && value !== undefined
        && value !== ""
        && !(Array.isArray(value) && value.length === 0))
      .map(([key, value]) => `
        <div class="detail-item ${Array.isArray(value) || key === "impact_graph" ? "wide" : ""}">
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
    if (key === "rag_documents" && Array.isArray(value)) {
      return `<ul class="value-list">${value.map(item => `<li>
        <strong>${escapeHtml(item.logical_name || "対象設計書")}</strong>
        ${item.document_ref ? `<small>${escapeHtml(item.document_ref)}</small>` : ""}
        ${item.heading_path ? `<p>${escapeHtml(Array.isArray(item.heading_path) ? item.heading_path.join(" / ") : item.heading_path)}</p>` : ""}
        ${item.summary ? `<p>${escapeHtml(item.summary)}</p>` : ""}
      </li>`).join("")}</ul>`;
    }
    if (key === "items" && Array.isArray(value)) {
      return `<ul class="value-list scope-list">${value.map(item => `<li>${renderScopeItem(item)}</li>`).join("")}</ul>`;
    }
    if (key === "impact_graph" && typeof value === "object" && value !== null) {
      return renderImpactGraph(value);
    }
    if (key === "commands" && Array.isArray(value)) {
      return `<ul class="value-list command-list">${value.map(item => `<li>${renderCommand(item)}</li>`).join("")}</ul>`;
    }
    if (key === "test_cases" && Array.isArray(value)) {
      return `<div class="plan-list">${value.map(renderTestCase).join("")}</div>
        <div class="test-case-edit-actions">
          <div><strong>生成された手順を変更しますか？</strong><small>自然言語で指定し、適用前に差分を確認できます。</small></div>
          <button type="button" class="secondary" data-open-test-case-revision>自然言語で修正</button>
        </div>`;
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
    const title = item.logical_name || item.text || item.summary || item.change_id || item.business_rule_id || item.evidence_id;
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

  function renderImpactGraph(graph) {
    const nodes = Array.isArray(graph.nodes) ? graph.nodes : [];
    const edges = Array.isArray(graph.edges) ? graph.edges : [];
    if (nodes.length === 0) return '<p class="empty-detail">影響ファイルはまだありません。</p>';
    const columns = {direct: 24, dependency: 335, test: 646};
    const rowCounts = {direct: 0, dependency: 0, test: 0};
    const positions = new Map();
    nodes.forEach((node, index) => {
      const group = node.directly_impacted ? "direct" : node.role === "test" ? "test" : "dependency";
      const position = {x: columns[group], y: 54 + rowCounts[group] * 104, group, index};
      rowCounts[group] += 1;
      positions.set(node.path, position);
    });
    const height = Math.max(190, 76 + Math.max(...Object.values(rowCounts)) * 104);
    const edgeHtml = edges.map(edge => {
      const from = positions.get(edge.from_path);
      const to = positions.get(edge.to_path);
      if (!from || !to) return "";
      const x1 = from.x + 230;
      const y1 = from.y + 34;
      const x2 = to.x;
      const y2 = to.y + 34;
      const reverse = x2 < x1;
      const startX = reverse ? from.x : x1;
      const endX = reverse ? to.x + 230 : x2;
      const bend = Math.max(34, Math.abs(endX - startX) * .45);
      const spansTwoColumns = Math.abs(from.x - to.x) > 400;
      const routeY = Math.max(y1, y2) + 52;
      const path = spansTwoColumns
        ? `M ${startX} ${y1} C ${startX + (reverse ? -bend : bend)} ${routeY}, ${endX + (reverse ? bend : -bend)} ${routeY}, ${endX} ${y2}`
        : `M ${startX} ${y1} C ${startX + (reverse ? -bend : bend)} ${y1}, ${endX + (reverse ? bend : -bend)} ${y2}, ${endX} ${y2}`;
      const labelX = (startX + endX) / 2;
      const labelY = spansTwoColumns ? routeY - 5 : (y1 + y2) / 2 - 7;
      return `<g class="impact-edge ${escapeHtml(edge.evidence_source || "code_graph")}">
        <path d="${path}" marker-end="url(#impact-arrow)"></path>
        <text x="${labelX}" y="${labelY}" text-anchor="middle">${escapeHtml(relationLabel(edge.relations || edge.relation))}</text>
      </g>`;
    }).join("");
    const nodeHtml = nodes.map((node, index) => {
      const position = positions.get(node.path);
      const filename = String(node.path || "対象ファイル").split("/").pop();
      const directory = String(node.path || "").slice(0, -String(filename).length).replace(/\/$/, "") || ".";
      return `<g class="impact-node ${position.group} ${index === 0 ? "selected" : ""}"
          transform="translate(${position.x} ${position.y})" role="button" tabindex="0"
          data-impact-node-index="${index}" aria-label="${escapeHtml(node.path)} の詳細を表示">
        <rect width="230" height="68" rx="10"></rect>
        <text class="node-file" x="14" y="27">${escapeHtml(truncate(filename, 27))}</text>
        <text class="node-dir" x="14" y="49">${escapeHtml(truncate(directory, 34))}</text>
        <circle cx="214" cy="16" r="5"></circle>
      </g>`;
    }).join("");
    const countLabel = `${Number(graph.visible_file_count || nodes.length)} ファイル・${Number(graph.relation_count || edges.length)} 関係`;
    return `<section class="impact-graph" data-impact-graph>
      <div class="impact-graph-heading">
        <div><strong>Code Graph に基づく影響関係</strong><small>${escapeHtml(countLabel)}</small></div>
        <div class="impact-legend"><span class="direct">変更対象</span><span class="dependency">依存</span><span class="test">テスト</span></div>
      </div>
      ${graph.truncated ? `<p class="graph-note">表示上限のため ${escapeHtml(graph.total_file_count)} ファイル中 ${escapeHtml(graph.visible_file_count)} ファイルを表示しています。</p>` : ""}
      <div class="impact-graph-scroll" tabindex="0" aria-label="影響ファイル関係図">
        <svg viewBox="0 0 900 ${height}" role="img" aria-label="変更対象、依存ファイル、関連テストの関係">
          <defs><marker id="impact-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z"></path></marker></defs>
          <text class="column-label" x="24" y="24">変更対象</text>
          <text class="column-label" x="335" y="24">依存ファイル</text>
          <text class="column-label" x="646" y="24">関連テスト</text>
          ${edgeHtml}${nodeHtml}
        </svg>
      </div>
      <div class="impact-node-details" data-impact-node-details>${impactNodeDetails(nodes[0])}</div>
    </section>`;
  }

  function impactNodeDetails(node) {
    if (!node || typeof node !== "object") return "";
    const symbols = Array.isArray(node.symbols) ? node.symbols : [];
    const tests = Array.isArray(node.related_tests) ? node.related_tests : [];
    const action = node.recommended_action ? `<span class="action-chip">${escapeHtml(actionLabel(node.recommended_action))}</span>` : "";
    return `<article>
      <div class="list-heading"><strong>${escapeHtml(node.path || "対象ファイル")}</strong>${action}</div>
      <small>${escapeHtml([roleLabel(node.role), node.language].filter(Boolean).join(" · "))}</small>
      ${node.rationale ? `<p><b>影響理由:</b> ${escapeHtml(node.rationale)}</p>` : ""}
      ${symbols.length ? `<div class="graph-detail-row"><b>対象シンボル</b><span>${symbols.map(value => `<code>${escapeHtml(value)}</code>`).join("")}</span></div>` : ""}
      ${tests.length ? `<div class="graph-detail-row"><b>関連テスト</b><span>${tests.map(value => `<code>${escapeHtml(value)}</code>`).join("")}</span></div>` : ""}
    </article>`;
  }

  function relationLabel(value) {
    if (Array.isArray(value)) return value.map(relationLabel).join(" / ");
    return ({imports: "import", calls: "呼出", implements: "実装", reads: "読取", writes: "書込", maps_to: "対応", tests: "テスト", navigates_to: "遷移", related_test: "関連テスト"})[value] || value || "依存";
  }

  function actionLabel(value) {
    return ({modify: "変更", add: "追加", delete: "削除", review_only: "確認のみ", no_change: "変更なし"})[value] || value;
  }

  function roleLabel(value) {
    return ({production: "本番コード", test: "テスト", config: "設定", migration: "DB 変更", contract: "契約", script: "スクリプト", unknown: "その他"})[value] || value || "その他";
  }

  function truncate(value, max) {
    const text = String(value || "");
    return text.length <= max ? text : `${text.slice(0, max - 1)}…`;
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

  return {escapeHtml, stageRail, stageDetails, impactNodeDetails, statusLabels, executorLabels};
});
