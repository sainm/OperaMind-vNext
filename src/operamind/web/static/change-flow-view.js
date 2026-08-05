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
    codex_fallback: "Codex fallback",
    operamind: "OperaMind"
  };

  const stageShortLabels = {
    requirement: "要件",
    document_change: "設計書",
    code_scope: "影響範囲",
    compile_test: "コード・テスト",
    ui_validation: "UI 検証",
    final_report: "レポート"
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
    ai_source: "AI 実行元",
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
    ui_test_cases: "UI テストケース",
    ui_test_plan_status: "UI TestPlan",
    result_revision: "結果リビジョン",
    command_evidence_status: "コンパイル・テスト",
    commands: "実行結果",
    test_data_plan_status: "テストデータ計画",
    test_data_status: "テストデータ",
    ui_status: "UI 検証",
    cleanup_status: "クリーンアップ",
    execution_actions: "再実行",
    generation_flows: "データ生成・UI 手順",
    data_bindings: "固定データ識別子",
    data_coverage_status: "実データ条件カバレッジ状態",
    data_coverage_percent: "実データ条件カバレッジ",
    data_coverage_proofs: "実 DB データ条件の検証結果",
    screenshots: "スクリーンショット",
    locator_failure_feedback: "Locator 検証の停止",
    closure_status: "完了判定",
    business_coverage_status: "業務カバレッジ状態",
    business_coverage_percent: "業務カバレッジ",
    business_coverage_items: "業務ルール別カバレッジ",
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
        <span><strong>${escapeHtml(stageShortLabels[stage.stage_id] || stage.label)}</strong><small>${escapeHtml(statusLabels[stage.status] || stage.status)}</small></span>
      </button>`).join("");
  }

  function stageDetails(stages, currentStage) {
    const publicStages = stages.filter(stage => publicStageIds.has(stage.stage_id));
    const selectedIndex = publicStages.findIndex(stage => stage.stage_id === currentStage);
    const currentIndex = selectedIndex >= 0 ? selectedIndex : publicStages.length - 1;
    return publicStages.map((stage, index) => {
      const blockers = (stage.blocking_reasons || []).map(
        reason => `<li>${escapeHtml(reason)}</li>`
      ).join("");
      const isFuture = index > currentIndex;
      const expanded = index === currentIndex || stage.status === "blocked";
      return `
        <details id="stage-${escapeHtml(stage.stage_id)}" class="stage-card ${escapeHtml(stage.status)} ${isFuture ? "future" : ""}" ${expanded ? "open" : ""}>
          <summary>
            <span class="stage-card-number">${String(index + 1).padStart(2, "0")}</span>
            <div>
              <div class="stage-title-line">
                <h3>${escapeHtml(stage.label)}</h3>
                <span class="status-badge ${escapeHtml(stage.status)}">${escapeHtml(statusLabels[stage.status] || stage.status)}</span>
              </div>
              <p>${escapeHtml(stage.summary)}</p>
            </div>
            <span class="executor">${escapeHtml(executorLabels[stage.executor] || stage.executor)}</span>
          </summary>
          <div class="stage-card-body">
            ${blockers ? `<div class="blocker-box"><strong>停止理由</strong><ul>${blockers}</ul></div>` : ""}
            ${isFuture
              ? '<p class="future-stage-message">前の工程が完了すると、この工程の内容と成果物を表示します。</p>'
              : `<div class="detail-grid">${renderDetails(stage.details || {})}</div>`}
          </div>
        </details>`;
    }).join("");
  }

  function nextAction(flow) {
    const stage = (flow.stages || []).find(item => item.stage_id === flow.current_stage)
      || (flow.stages || [])[0];
    if (!stage) return "";
    const confirmation = stage.details && stage.details.confirmation;
    const blockers = Array.isArray(stage.blocking_reasons) ? stage.blocking_reasons : [];
    const tone = confirmation ? "confirmation" : stage.status;
    const title = confirmation
      ? confirmation.stage_label || `${stage.label}の確認`
      : stage.status === "blocked"
        ? "対応が必要です"
        : stage.status === "running"
          ? `${stage.label}を実行しています`
          : stage.status === "completed"
            ? "変更フローが完了しました"
            : `${stage.label}を開始します`;
    const message = confirmation
      ? confirmation.message || "内容を確認してください。"
      : blockers[0] || stage.summary || "次の処理を準備しています。";
    return `<section class="next-action-panel ${escapeHtml(tone)}" aria-label="現在のアクション">
      <div class="next-action-icon" aria-hidden="true">${confirmation ? "✓" : stage.status === "blocked" ? "!" : stage.status === "completed" ? "✓" : "→"}</div>
      <div class="next-action-copy">
        <span class="eyebrow">NOW · ${escapeHtml(executorLabels[stage.executor] || stage.executor)}</span>
        <h2>${escapeHtml(title)}</h2>
        <p>${escapeHtml(message)}</p>
        ${blockers.length > 1 ? `<ul>${blockers.slice(1).map(reason => `<li>${escapeHtml(reason)}</li>`).join("")}</ul>` : ""}
      </div>
      ${confirmation ? `<div class="confirmation-actions">
        <button type="button" class="primary" data-confirm-checkpoint="${escapeHtml(confirmation.checkpoint)}" data-subject-digest="${escapeHtml(confirmation.subject_digest)}">確認して進む</button>
        <button type="button" class="secondary" data-reject-checkpoint="${escapeHtml(confirmation.checkpoint)}" data-subject-digest="${escapeHtml(confirmation.subject_digest)}">差し戻す</button>
      </div>` : ""}
    </section>`;
  }

  function requirementTitle(value) {
    const text = String(value || "").trim();
    if (!text) return "変更フロー";
    const firstSentence = text.split(/[。\n]/, 1)[0].trim();
    return truncate(firstSentence || text, 64);
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
    if (key === "locator_failure_feedback" && typeof value === "object" && value !== null) {
      const failures = Array.isArray(value.failures) ? value.failures : [];
      return `<div class="blocker-box"><strong>操作前検証で停止</strong>
        <ul>${failures.map(item => `<li>${escapeHtml(item.failure_reason || "Locator 検証に失敗しました。")}</li>`).join("")}</ul>
        <p>${escapeHtml(value.next_action || "新しい UI TestPlan Revision を確認してください。")}</p>
      </div>`;
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
    if (key === "business_coverage_items" && Array.isArray(value)) {
      return `<ul class="value-list coverage-list">${value.map(item => `<li>
        <div class="list-heading">
          <strong>${escapeHtml(item.text || "業務ルール")}</strong>
          <span class="status-badge ${escapeHtml(item.status || "uncovered")}">${escapeHtml(item.status === "covered" ? "カバー済み" : "未カバー")}</span>
        </div>
        <small>テストケース ${escapeHtml(item.test_case_count || 0)} 件 · 検証基準 ${escapeHtml(item.criterion_count || 0)} 件</small>
      </li>`).join("")}</ul>`;
    }
    if ((key === "test_cases" || key === "ui_test_cases") && Array.isArray(value)) {
      return `<div class="plan-list">${value.map(renderTestCase).join("")}</div>
        <div class="test-case-edit-actions">
          <div><strong>生成された手順を変更しますか？</strong><small>自然言語で指定し、適用前に差分を確認できます。</small></div>
          <button type="button" class="secondary" data-open-test-case-revision>自然言語で修正</button>
        </div>`;
    }
    if (key === "generation_flows" && Array.isArray(value)) {
      return `<div class="plan-list">${value.map(renderGenerationFlow).join("")}</div>
        <div class="test-case-edit-actions">
          <div><strong>生成・変数・断言・クリーンアップを変更しますか？</strong><small>自然言語で指定し、完全な計画を再生成する前に差分を確認できます。</small></div>
          <button type="button" class="secondary" data-open-test-case-revision>自然言語で修正</button>
        </div>`;
    }
    if (key === "data_bindings" && Array.isArray(value)) {
      return `<div class="plan-list">${value.map(renderDataBinding).join("")}</div>`;
    }
    if (key === "data_coverage_proofs" && Array.isArray(value)) {
      return `<div class="plan-list">${value.map(renderDataCoverageProof).join("")}</div>`;
    }
    if (key === "execution_actions" && typeof value === "object" && value !== null) {
      return value.can_rerun && value.rerun_run_id
        ? `<button type="button" class="primary" data-rerun-test-data="${escapeHtml(value.rerun_run_id)}">同じ計画で再実行</button>`
        : '<span class="evidence-chip">再実行できません</span>';
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

  function renderDataBinding(item) {
    if (typeof item !== "object" || item === null) return escapeHtml(item);
    const business = Array.isArray(item.business_unique_keys)
      ? item.business_unique_keys.map(value => `${value.name}: ${value.value}`).join(" · ")
      : "";
    const screenValues = Array.isArray(item.screen_identity_values) && item.screen_identity_values.length
      ? item.screen_identity_values
      : item.screen_key ? [item.screen_key] : [];
    const screen = screenValues.map(value => `${value.name}: ${value.value}`).join(" · ");
    const provider = item.identity_provider_type || "";
    return `<article class="plan-card">
      <div class="list-heading">
        <strong>${escapeHtml(item.test_data_id || "テストデータ")}</strong>
        <span class="status-badge passed">一意 ${escapeHtml(item.match_count || 0)} 件</span>
      </div>
      <small>${escapeHtml(item.binding_mode === "generated" ? "生成データ" : "既存データを接管")} · 実行時に固定${provider ? ` · ${escapeHtml(provider)}` : ""}</small>
      <dl class="field-deltas">
        <div><dt>業務一意キー</dt><dd>${escapeHtml(business || "-")}</dd></div>
        <div><dt>画面識別値</dt><dd>${escapeHtml(screen || "-")}</dd></div>
      </dl>
      <small>${item.frozen_at ? `固定日時 ${escapeHtml(item.frozen_at)}` : "実行時に固定"}</small>
    </article>`;
  }

  function renderDataCoverageProof(item) {
    if (typeof item !== "object" || item === null) return escapeHtml(item);
    const status = item.status === "passed" ? "passed" : "blocked";
    return `<article class="plan-card">
      <div class="list-heading">
        <strong>${escapeHtml(item.condition_id || "データ条件")}</strong>
        <span class="status-badge ${status}">${escapeHtml(item.status || "未検証")}</span>
      </div>
      <small>${escapeHtml(item.test_case_ref || "TestCase")} · ${escapeHtml(item.test_data_id || "TestData")}</small>
      <dl class="field-deltas">
        <div><dt>AcceptanceCriteria</dt><dd>${escapeHtml(item.criterion_ref || "-")}</dd></div>
        <div><dt>実 DB Path</dt><dd>${escapeHtml(item.path || "-")}</dd></div>
        <div><dt>条件</dt><dd>${escapeHtml(item.operator || "-")}</dd></div>
        <div><dt>期待値</dt><dd>${escapeHtml(JSON.stringify(item.expected))}</dd></div>
        <div><dt>実測値</dt><dd>${escapeHtml(JSON.stringify(item.actual))}</dd></div>
        <div><dt>Evidence</dt><dd>${escapeHtml(item.evidence_ref || "-")}</dd></div>
      </dl>
      ${item.failure_reason ? `<p class="error-message">${escapeHtml(item.failure_reason)}</p>` : ""}
    </article>`;
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
    const mappedCount = Number(item.mapped_test_step_count || 0);
    const fallback = item.computer_use_fallback;
    return `<article class="flow-step">
      <div class="flow-step-heading">
        <span class="step-sequence">${escapeHtml(item.sequence || "–")}</span>
        <div><strong>${escapeHtml(item.business_action || "処理")}</strong><small>${escapeHtml(item.channel || "")}</small></div>
        <span class="status-badge ${escapeHtml(status)}">${escapeHtml(statusLabels[status] || status)}</span>
      </div>
      ${inputs.length ? `<p><b>入力変数:</b> ${inputs.map(escapeHtml).join(", ")}</p>` : ""}
      ${outputs.length ? `<p><b>出力変数:</b> ${outputs.map(escapeHtml).join(", ")}</p>` : ""}
      ${mappedCount ? `<p><b>自然言語手順との対応:</b> ${escapeHtml(mappedCount)} 件</p>` : ""}
      ${fallback ? `<div class="blocker-box"><strong>AI 画面操作フォールバック</strong><p>${escapeHtml(fallback.objective || "")}</p><small>${escapeHtml(fallback.reason || "")} · 最大 ${escapeHtml(fallback.max_actions || 0)} 操作</small></div>` : ""}
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

  return {
    escapeHtml,
    stageRail,
    stageDetails,
    nextAction,
    requirementTitle,
    impactNodeDetails,
    statusLabels,
    executorLabels
  };
});
