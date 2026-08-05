"use strict";

const view = window.OperaMindChangeFlow;
const vscodeLink = window.OperaMindVsCodeLink;
const state = {
  projects: [],
  projectId: null,
  requestId: null,
  flow: null,
  pollTimer: null,
  projectPollTimer: null,
  projectDialogMode: "create",
  revisionProposal: null,
  impactNodePath: null,
  documentLearning: null,
  identityProfilesLoaded: false,
  commandKeys: new Map()
};

const elements = {
  projectSelect: document.getElementById("projectSelect"),
  openVsCodeButton: document.getElementById("openVsCodeButton"),
  requestList: document.getElementById("requestList"),
  notice: document.getElementById("notice"),
  emptyState: document.getElementById("emptyState"),
  flowWorkspace: document.getElementById("flowWorkspace"),
  pageTitle: document.getElementById("pageTitle"),
  pageRequestId: document.getElementById("pageRequestId"),
  pageDescription: document.getElementById("pageDescription"),
  flowStatus: document.getElementById("flowStatus"),
  progressValue: document.getElementById("progressValue"),
  progressBar: document.getElementById("progressBar"),
  flowStages: document.getElementById("flowStages"),
  stageDetails: document.getElementById("stageDetails"),
  nextActionPanel: document.getElementById("nextActionPanel"),
  projectSummary: document.getElementById("projectSummary"),
  projectSourceKind: document.getElementById("projectSourceKind"),
  projectWorkspaceSummary: document.getElementById("projectWorkspaceSummary"),
  projectTestBaseUrlSummary: document.getElementById("projectTestBaseUrlSummary"),
  projectTargetDataSummary: document.getElementById("projectTargetDataSummary"),
  projectQualitySummary: document.getElementById("projectQualitySummary"),
  projectOnboardingSummary: document.getElementById("projectOnboardingSummary"),
  projectDocumentRootSummary: document.getElementById("projectDocumentRootSummary"),
  editProjectButton: document.getElementById("editProjectButton"),
  projectPreflightButton: document.getElementById("projectPreflightButton"),
  documentLearningButton: document.getElementById("documentLearningButton"),
  projectRescanButton: document.getElementById("projectRescanButton"),
  projectReindexButton: document.getElementById("projectReindexButton"),
  projectRetryButton: document.getElementById("projectRetryButton"),
  existingTestDataButton: document.getElementById("existingTestDataButton"),
  fixedDataIdentifiersButton: document.getElementById("fixedDataIdentifiersButton"),
  existingTestDataDialog: document.getElementById("existingTestDataDialog"),
  existingTestDataForm: document.getElementById("existingTestDataForm"),
  existingDataName: document.getElementById("existingDataName"),
  existingBusinessValue: document.getElementById("existingBusinessValue"),
  existingTestCaseRef: document.getElementById("existingTestCaseRef"),
  existingRetainAfterTest: document.getElementById("existingRetainAfterTest"),
  submitExistingTestDataButton: document.getElementById("submitExistingTestDataButton"),
  existingTestDataList: document.getElementById("existingTestDataList"),
  fixedDataIdentifiersDialog: document.getElementById("fixedDataIdentifiersDialog"),
  plannedDataCount: document.getElementById("plannedDataCount"),
  frozenDataCount: document.getElementById("frozenDataCount"),
  plannedDataIdentifiers: document.getElementById("plannedDataIdentifiers"),
  frozenDataIdentifiers: document.getElementById("frozenDataIdentifiers"),
  documentLearningDialog: document.getElementById("documentLearningDialog"),
  documentLearningStatus: document.getElementById("documentLearningStatus"),
  documentLearningContent: document.getElementById("documentLearningContent"),
  relearnDocumentsButton: document.getElementById("relearnDocumentsButton"),
  openLearningVsCodeButton: document.getElementById("openLearningVsCodeButton"),
  confirmDocumentLearningButton: document.getElementById("confirmDocumentLearningButton"),
  emptyStateTitle: document.getElementById("emptyStateTitle"),
  emptyStateDescription: document.getElementById("emptyStateDescription"),
  emptyNewProjectButton: document.getElementById("emptyNewProjectButton"),
  emptyNewRequestButton: document.getElementById("emptyNewRequestButton"),
  projectDialog: document.getElementById("projectDialog"),
  projectDialogEyebrow: document.getElementById("projectDialogEyebrow"),
  projectDialogTitle: document.getElementById("projectDialogTitle"),
  projectForm: document.getElementById("projectForm"),
  projectId: document.getElementById("projectId"),
  projectName: document.getElementById("projectName"),
  projectWorkspaceRoot: document.getElementById("projectWorkspaceRoot"),
  projectDocumentRoots: document.getElementById("projectDocumentRoots"),
  projectTestBaseUrl: document.getElementById("projectTestBaseUrl"),
  projectTargetDataDialect: document.getElementById("projectTargetDataDialect"),
  projectTargetDataAlias: document.getElementById("projectTargetDataAlias"),
  projectTargetDataDsn: document.getElementById("projectTargetDataDsn"),
  projectTargetDataBindings: document.getElementById("projectTargetDataBindings"),
  projectDataIdentityProfiles: document.getElementById("projectDataIdentityProfiles"),
  projectTargetDataStatus: document.getElementById("projectTargetDataStatus"),
  projectFormStatus: document.getElementById("projectFormStatus"),
  submitProjectButton: document.getElementById("submitProjectButton"),
  requestDialog: document.getElementById("requestDialog"),
  requestForm: document.getElementById("requestForm"),
  requestId: document.getElementById("requestId"),
  requirementText: document.getElementById("requirementText"),
  requirementCount: document.getElementById("requirementCount"),
  requestProjectName: document.getElementById("requestProjectName"),
  requestWorkspaceSummary: document.getElementById("requestWorkspaceSummary"),
  requestDocumentSummary: document.getElementById("requestDocumentSummary"),
  requestFormStatus: document.getElementById("requestFormStatus"),
  submitRequestButton: document.getElementById("submitRequestButton"),
  testCaseRevisionDialog: document.getElementById("testCaseRevisionDialog"),
  testCaseRevisionForm: document.getElementById("testCaseRevisionForm"),
  testCaseRevisionInstruction: document.getElementById("testCaseRevisionInstruction"),
  testCaseRevisionPreview: document.getElementById("testCaseRevisionPreview"),
  confirmTestCaseRevisionButton: document.getElementById("confirmTestCaseRevisionButton")
};

async function api(path, options = {}) {
  const {idempotencyScope, ...requestOptions} = options;
  const response = await fetch(path, {
    ...requestOptions,
    headers: {
      ...(requestOptions.method && requestOptions.method !== "GET"
        ? {"Content-Type": "application/json"}
        : {}),
      "X-OperaMind-Actor": "local-user",
      ...(requestOptions.method && requestOptions.method !== "GET"
        ? {"Idempotency-Key": commandKey(idempotencyScope || path)}
        : {}),
      ...(requestOptions.headers || {})
    }
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.details?.reason || body.message || `HTTP ${response.status}`);
  return body;
}

function commandKey(scope) {
  if (!state.commandKeys.has(scope)) state.commandKeys.set(scope, crypto.randomUUID());
  return state.commandKeys.get(scope);
}

function clearCommandKey(scope) {
  state.commandKeys.delete(scope);
}

function showNotice(message, kind = "info") {
  elements.notice.textContent = message;
  elements.notice.className = `notice ${kind}`;
  window.setTimeout(() => elements.notice.classList.add("hidden"), 5000);
}

async function loadProjects(preferredId = null) {
  const result = await api("/api/v1/projects");
  const projects = result.projects || [];
  state.projects = projects;
  elements.projectSelect.innerHTML = projects.map(project =>
    `<option value="${view.escapeHtml(project.project_id)}">${view.escapeHtml(project.name || project.project_id)}</option>`
  ).join("");
  const selectedId = projects.some(project => project.project_id === preferredId)
    ? preferredId
    : projects.some(project => project.project_id === state.projectId)
      ? state.projectId
      : projects[0]?.project_id;
  elements.projectSelect.value = selectedId || "";
  state.projectId = selectedId || null;
  renderProjectSummary(projects.find(project => project.project_id === state.projectId));
  scheduleProjectPolling(projects.some(project =>
    ["queued", "running", "waiting_for_profile"].includes(project.onboarding?.status)
  ));
  if (!state.projectId) {
    elements.requestList.innerHTML = '<p class="empty">登録済みプロジェクトがありません。</p>';
    showEmptyState();
    return;
  }
  await loadRequests();
}

function renderProjectSummary(project) {
  elements.projectSummary.classList.toggle("hidden", !project?.workspace_root);
  elements.openVsCodeButton.disabled = !project?.workspace_root;
  elements.openVsCodeButton.title = project?.workspace_root
    ? `${project.workspace_root} を VS Code で開く`
    : "コード Workspace を設定してください";
  if (!project?.workspace_root) return;
  const baselines = project.source_git_baselines || [];
  const managedLocally = project.source_control_kind !== "git"
    || baselines.some(item => item.management_kind === "operamind_local_git");
  elements.projectSourceKind.textContent = managedLocally ? "OperaMind 内部 Git" : "Git";
  elements.projectWorkspaceSummary.textContent = project.workspace_root;
  elements.projectTestBaseUrlSummary.textContent = project.test_base_url
    ? `UI テスト: ${project.test_base_url}`
    : "UI テスト URL 未設定";
  const targetData = project.target_data_profile;
  elements.projectTargetDataSummary.textContent = targetData
    ? `DB データ準備: ${targetData.dialect || "postgresql"} / ${targetData.connection_alias} · Binding ${Number(targetData.query_binding_ids?.length || 0).toLocaleString("ja-JP")} 件${targetData.secret_configured ? "" : " · Secret 未設定"}`
    : "DB データ準備: 未設定（HTTP/UI のみ）";
  const targetProject = project.target_project || {};
  const qualityMissing = targetProject.quality_missing_signals || [];
  elements.projectQualitySummary.textContent = targetProject.quality_readiness === "ready"
    ? "コード品質基線: ready"
    : qualityMissing.length
      ? `コード品質基線: blocked · ${qualityMissing.join("、")}`
      : "コード品質基線: 未判定";
  const onboarding = project.onboarding || {};
  const stageLabels = {discover: "構造抽出", learn: "設計書学習", documents: "Canonical 文書", index: "RAG 索引", complete: "完了"};
  const statusLabels = {queued: "待機中", running: "実行中", waiting_for_profile: "学習確認待ち", ready: "準備完了", failed: "失敗", superseded: "設定更新済み"};
  const onboardingCounts = onboarding.status === "ready"
    ? ` · 設計書 ${Number(onboarding.document_count || 0).toLocaleString("ja-JP")} 件 · RAG Vector ${Number(onboarding.generated_vector_count || 0).toLocaleString("ja-JP")} 件`
    : "";
  elements.projectOnboardingSummary.className = `project-onboarding-summary ${onboarding.status || "queued"}`;
  elements.projectOnboardingSummary.textContent = onboarding.status
    ? `Onboarding: ${statusLabels[onboarding.status] || onboarding.status} · ${stageLabels[onboarding.current_stage] || onboarding.current_stage}${onboardingCounts}${onboarding.failure_reason ? ` · ${onboarding.failure_reason}` : ""}`
    : "Onboarding はまだ開始されていません。";
  elements.projectRetryButton.classList.toggle("hidden", onboarding.status !== "failed");
  elements.projectReindexButton.disabled = onboarding.status !== "ready";
  elements.projectRescanButton.disabled = ["queued", "running", "waiting_for_profile"].includes(onboarding.status);
  elements.documentLearningButton.disabled = !project.document_roots?.length;
  elements.projectDocumentRootSummary.innerHTML = (project.document_roots || []).map(root => {
    const binding = baselines.find(item => item.source_kind === "document" && item.configured_root === root);
    const baselineLabel = binding?.baseline_revision
      ? `Git 基線 · ${binding.baseline_revision.slice(0, 8)}`
      : "旧登録 · Git 基線情報なし";
    return `<li>${view.escapeHtml(root)}<small>${view.escapeHtml(baselineLabel)}</small></li>`;
  }).join("");
}

async function openExistingTestDataPage() {
  if (!state.projectId) return showNotice("先にプロジェクトを選択してください。", "error");
  elements.existingTestDataDialog.showModal();
  await loadExistingTestData();
}

async function loadExistingTestData() {
  if (!state.projectId) return;
  elements.existingTestDataList.innerHTML = '<p class="empty">実データを確認しています。</p>';
  const result = await api(`/api/v1/projects/${encodeURIComponent(state.projectId)}/existing-test-data`);
  const items = result.registrations || [];
  elements.existingTestDataList.innerHTML = items.length
    ? items.map(renderExistingTestData).join("")
    : '<p class="empty">登録済みの既存テストデータはありません。</p>';
  for (const button of elements.existingTestDataList.querySelectorAll("[data-confirm-registration]")) {
    button.addEventListener("click", () => confirmExistingTestData(button.dataset.confirmRegistration));
  }
}

function renderExistingTestData(item) {
  const statusLabels = {candidate: "確認待ち", confirmed: "確認済み", blocked: "阻断"};
  const summary = Object.entries(item.business_summary || {}).map(([name, value]) =>
    `<div><dt>${view.escapeHtml(name)}</dt><dd>${view.escapeHtml(String(value))}</dd></div>`
  ).join("");
  const reasons = (item.blocking_reasons || []).map(reason =>
    `<li>${view.escapeHtml(reason)}</li>`
  ).join("");
  return `<article class="data-identity-card ${view.escapeHtml(item.status)}">
    <header>
      <div><strong>${view.escapeHtml(item.data_name)}</strong><small>${view.escapeHtml(item.test_case_ref)}</small></div>
      <span class="status-badge ${item.status === "confirmed" ? "success" : item.status === "blocked" ? "error" : "neutral"}">${view.escapeHtml(statusLabels[item.status] || item.status)}</span>
    </header>
    <p class="data-business-key"><span>業務一意値</span><strong>${view.escapeHtml(item.business_unique_value)}</strong></p>
    ${summary ? `<dl class="business-summary">${summary}</dl>` : ""}
    <div class="data-card-meta">
      <span>${view.escapeHtml(item.provider_type || "Provider 未確定")}</span>
      <span>一致 ${item.match_count == null ? "未確定" : Number(item.match_count).toLocaleString("ja-JP")} 件</span>
      <span>Evidence ${Number(item.evidence_count || 0).toLocaleString("ja-JP")} 件</span>
      <span>${item.retain_after_test ? "終了後も保持" : "終了後に Cleanup"}</span>
    </div>
    ${reasons ? `<ul class="data-blocking-reasons">${reasons}</ul>` : ""}
    ${item.status === "candidate" ? `<div class="data-card-actions"><button class="primary" type="button" data-confirm-registration="${view.escapeHtml(item.registration_id)}">この一件を確認</button></div>` : ""}
  </article>`;
}

async function registerExistingTestData(event) {
  event.preventDefault();
  if (!state.projectId) return;
  const scope = `existing-test-data:${state.projectId}:${crypto.randomUUID()}`;
  elements.submitExistingTestDataButton.disabled = true;
  try {
    const result = await api(`/api/v1/projects/${encodeURIComponent(state.projectId)}/existing-test-data`, {
      method: "POST",
      idempotencyScope: scope,
      body: JSON.stringify({
        data_name: elements.existingDataName.value.trim(),
        business_unique_value: elements.existingBusinessValue.value.trim(),
        test_case_ref: elements.existingTestCaseRef.value.trim(),
        retain_after_test: elements.existingRetainAfterTest.checked
      })
    });
    clearCommandKey(scope);
    elements.existingTestDataForm.reset();
    await loadExistingTestData();
    showNotice(
      result.registration?.status === "candidate"
        ? "実データが一件だけ一致しました。業務摘要を確認してください。"
        : "実データを一意に確認できないため阻断しました。",
      result.registration?.status === "candidate" ? "success" : "error"
    );
  } catch (error) {
    showNotice(error.message, "error");
  } finally {
    elements.submitExistingTestDataButton.disabled = false;
  }
}

async function confirmExistingTestData(registrationId) {
  const scope = `existing-test-data-confirm:${state.projectId}:${registrationId}`;
  try {
    await api(`/api/v1/projects/${encodeURIComponent(state.projectId)}/existing-test-data/${encodeURIComponent(registrationId)}/confirm`, {
      method: "POST",
      idempotencyScope: scope,
      body: "{}"
    });
    clearCommandKey(scope);
    await loadExistingTestData();
    showNotice("既存データを adopted TestDataPlan データとして確認しました。", "success");
  } catch (error) {
    showNotice(error.message, "error");
  }
}

async function openFixedDataIdentifiersPage() {
  if (!state.projectId) return showNotice("先にプロジェクトを選択してください。", "error");
  elements.fixedDataIdentifiersDialog.showModal();
  elements.plannedDataIdentifiers.innerHTML = '<p class="empty">計画を読み込んでいます。</p>';
  elements.frozenDataIdentifiers.innerHTML = '<p class="empty">凍結結果を読み込んでいます。</p>';
  const result = await api(`/api/v1/projects/${encodeURIComponent(state.projectId)}/fixed-data-identifiers`);
  elements.plannedDataCount.textContent = `${Number(result.planned_count || 0).toLocaleString("ja-JP")} 件`;
  elements.frozenDataCount.textContent = `${Number(result.frozen_count || 0).toLocaleString("ja-JP")} 件`;
  elements.plannedDataIdentifiers.innerHTML = result.planned?.length
    ? result.planned.map(renderPlannedDataIdentifier).join("")
    : '<p class="empty">確認済みの計画データはありません。</p>';
  elements.frozenDataIdentifiers.innerHTML = result.frozen?.length
    ? result.frozen.map(renderFrozenDataIdentifier).join("")
    : '<p class="empty">まだ Run で凍結されたデータはありません。</p>';
}

function renderPlannedDataIdentifier(item) {
  return `<article class="data-identity-card planned">
    <header><div><strong>${view.escapeHtml(item.data_name)}</strong><small>${(item.test_case_refs || []).map(view.escapeHtml).join("、")}</small></div><span class="status-badge neutral">実行前</span></header>
    <p class="data-business-key"><span>業務一意値</span><strong>${view.escapeHtml(item.business_unique_value)}</strong></p>
    <div class="data-card-meta"><span>${view.escapeHtml(item.provider_type || "未確定")}</span><span>${item.retain_after_test ? "終了後も保持" : "終了後に Cleanup"}</span></div>
  </article>`;
}

function renderFrozenDataIdentifier(item) {
  const businessValues = [...(item.business_values || []), ...(item.screen_values || [])];
  const values = businessValues.map(value =>
    `<div><dt>${view.escapeHtml(value.name || "識別値")}</dt><dd>${view.escapeHtml(String(value.value ?? ""))}</dd></div>`
  ).join("");
  const usages = (item.usages || []).map(usage =>
    `<li><strong>${view.escapeHtml(usage.flow_id)}</strong><span>${view.escapeHtml(usage.phase)} / ${view.escapeHtml(usage.step_id)} / ${view.escapeHtml(usage.status)}</span></li>`
  ).join("");
  const evidence = (item.evidence || []).map(value =>
    `<li><strong>${view.escapeHtml(value.evidence_type)}</strong><span>${view.escapeHtml(value.phase)} / ${view.escapeHtml(value.step_id)}</span><small>${view.escapeHtml(value.evidence_ref)}</small></li>`
  ).join("");
  return `<article class="data-identity-card frozen">
    <header><div><strong>${view.escapeHtml(item.data_name)}</strong><small>${view.escapeHtml(item.test_data_token || "Token 未記録")}</small></div><span class="status-badge success">凍結済み</span></header>
    ${values ? `<dl class="business-summary">${values}</dl>` : ""}
    <div class="data-card-meta"><span>Run ${view.escapeHtml(item.run_id)}</span><span>${view.escapeHtml(item.provider_type || "Provider 不明")}</span><span>Cleanup ${view.escapeHtml(item.cleanup?.status || "未実行")}</span></div>
    <details><summary>使用箇所 ${Number(item.usages?.length || 0).toLocaleString("ja-JP")} 件</summary><ul class="binding-trace-list">${usages || "<li>使用記録なし</li>"}</ul></details>
    <details><summary>Evidence ${Number(item.evidence?.length || 0).toLocaleString("ja-JP")} 件</summary><ul class="binding-trace-list">${evidence || "<li>Evidence なし</li>"}</ul></details>
  </article>`;
}

async function openDocumentLearningDialog() {
  if (!state.projectId) return;
  const result = await api(`/api/v1/projects/${encodeURIComponent(state.projectId)}/document-learning`);
  state.documentLearning = result.learning || null;
  renderDocumentLearning(state.documentLearning);
  elements.documentLearningDialog.showModal();
}

function renderDocumentLearning(learning) {
  if (!learning) {
    elements.documentLearningStatus.textContent = "まだ学習タスクがありません。構造を再学習してください。";
    elements.documentLearningStatus.className = "learning-status waiting";
    elements.documentLearningContent.innerHTML = "";
    elements.confirmDocumentLearningButton.disabled = true;
    return;
  }
  const statusLabels = {
    pending: "VS Code 待ち", claimed: "Copilot 接続済み", in_progress: "学習中",
    draft_ready: "確認可能", confirmed: "適用済み", failed: "失敗",
    cancelled: "取消", superseded: "旧バージョン"
  };
  const coverage = Number(learning.coverage_percent || 0);
  const ambiguities = Number(learning.ambiguity_count || 0);
  elements.documentLearningStatus.className = `learning-status ${learning.status}`;
  elements.documentLearningStatus.innerHTML = `
    <strong>${view.escapeHtml(statusLabels[learning.status] || learning.status)}</strong>
    <span>Sample ${Number(learning.sample_count || 0).toLocaleString("ja-JP")} 件 · Coverage ${coverage.toFixed(2)}% · 曖昧 ${ambiguities} 件</span>
    <div class="learning-meter" aria-label="サンプル網羅率 ${coverage.toFixed(2)}%"><span style="width:${Math.min(100, coverage)}%"></span></div>`;
  const draft = learning.draft || {};
  const profiles = draft.profiles || [];
  const assignments = draft.document_assignments || [];
  const ambiguityItems = draft.ambiguities || [];
  elements.documentLearningContent.innerHTML = profiles.length ? `
    <div class="learning-profile-list">${profiles.map(profile => `
      <article class="learning-profile-card">
        <header><strong>${view.escapeHtml(profile.document_type || profile.profile_id)}</strong><span>v${view.escapeHtml(profile.profile_version || "—")}</span></header>
        <small>${view.escapeHtml(profile.profile_id || "")}</small>
        ${(profile.variants || []).map(variant => `
          <section><h3>${view.escapeHtml(variant.variant_id || "Variant")}</h3>
            <dl><dt>Field Mapping</dt><dd>${Object.entries(variant.field_aliases || {}).map(([field, aliases]) => `<code>${view.escapeHtml(field)} ← ${view.escapeHtml((aliases || []).join(" / "))}</code>`).join("") || "—"}</dd>
            <dt>Stable Key</dt><dd>${(variant.stable_key_fields || []).map(field => `<code>${view.escapeHtml(field)}</code>`).join("") || "—"}</dd></dl>
          </section>`).join("")}
      </article>`).join("")}</div>
    <div class="learning-evidence"><strong>Sample 割当 ${assignments.length} 件</strong><span>すべての実ファイルは Profile/Variant で再検証されます。</span></div>
    ${ambiguityItems.length ? `<ul class="learning-ambiguities">${ambiguityItems.map(item => `<li>${view.escapeHtml(item.description || "曖昧な構造")}</li>`).join("")}</ul>` : ""}
  ` : `<p class="empty">Copilot が Project 専用 Profile 草案を返すと、Field Mapping と Stable Key がここに表示されます。</p>`;
  elements.confirmDocumentLearningButton.disabled = !(
    learning.status === "draft_ready" && coverage === 100 && ambiguities === 0
  );
}

async function confirmDocumentLearning() {
  const learning = state.documentLearning;
  if (!state.projectId || !learning?.learning_run_id) return;
  const scope = `project-document-learning:${state.projectId}:${learning.learning_run_id}:confirm`;
  try {
    await api(`/api/v1/projects/${encodeURIComponent(state.projectId)}/document-learning/confirm`, {
      method: "POST",
      idempotencyScope: scope,
      body: JSON.stringify({learning_run_id: learning.learning_run_id})
    });
    clearCommandKey(scope);
    showNotice("設計書 Profile を適用しました。Canonical 化と RAG 索引を続行します。", "success");
    elements.documentLearningDialog.close();
    await loadProjects(state.projectId);
  } catch (error) {
    showNotice(error.message, "error");
  }
}

function openSelectedProjectInVsCode() {
  const project = state.projects.find(item => item.project_id === state.projectId);
  if (!project?.workspace_root) {
    showNotice("コード Workspace を設定してください。", "error");
    return;
  }
  try {
    window.location.assign(vscodeLink.buildOpenUrl(project.workspace_root, state.requestId));
  } catch (error) {
    showNotice(error.message, "error");
  }
}

async function loadRequests(preferredId = null) {
  if (!state.projectId) return;
  const result = await api(`/api/v1/change-requests?project_id=${encodeURIComponent(state.projectId)}`);
  const requests = result.change_requests || [];
  elements.requestList.innerHTML = requests.length ? requests.map(request => {
    const active = request.change_request_id === (preferredId || state.requestId);
    return `<button type="button" class="request-item ${active ? "active" : ""}" data-request-id="${view.escapeHtml(request.change_request_id)}">
      <strong>${view.escapeHtml(request.requirement_text || request.change_request_id)}</strong>
      <span>${view.escapeHtml(request.change_request_id)}</span>
    </button>`;
  }).join("") : '<p class="empty">変更要件はまだありません。</p>';

  for (const button of elements.requestList.querySelectorAll("[data-request-id]")) {
    button.addEventListener("click", () => selectRequest(button.dataset.requestId));
  }
  const nextId = preferredId || (requests.some(item => item.change_request_id === state.requestId) ? state.requestId : requests[0]?.change_request_id);
  if (nextId) await selectRequest(nextId);
  else showEmptyState();
}

async function selectRequest(requestId) {
  if (state.requestId !== requestId) state.impactNodePath = null;
  state.requestId = requestId;
  for (const item of elements.requestList.querySelectorAll(".request-item")) {
    item.classList.toggle("active", item.dataset.requestId === requestId);
  }
  await loadFlow();
}

async function loadFlow() {
  if (!state.requestId) return showEmptyState();
  const flow = await api(`/api/v1/change-requests/${encodeURIComponent(state.requestId)}/flow`);
  state.flow = flow;
  renderFlow(flow);
  schedulePolling(flow.status === "in_progress");
}

function renderFlow(flow) {
  elements.emptyState.classList.add("hidden");
  elements.flowWorkspace.classList.remove("hidden");
  const requirement = flow.stages.find(stage => stage.stage_id === "requirement");
  const requirementText = requirement?.details?.requirement_text || requirement?.summary || "変更フロー";
  const currentStage = flow.stages.find(stage => stage.stage_id === flow.current_stage);
  const confirmationWaiting = Boolean(currentStage?.details?.confirmation);
  elements.pageTitle.textContent = view.requirementTitle(requirementText);
  elements.pageRequestId.textContent = flow.change_request_id;
  elements.pageDescription.textContent = requirementText;
  elements.flowStatus.textContent = confirmationWaiting
    ? "確認待ち"
    : view.statusLabels[flow.status] || flow.status;
  elements.flowStatus.className = `status-badge ${confirmationWaiting ? "waiting" : flow.status}`;
  elements.nextActionPanel.innerHTML = view.nextAction(flow);
  elements.progressValue.textContent = `${flow.progress_percent}%`;
  elements.progressBar.style.width = `${flow.progress_percent}%`;
  elements.flowStages.innerHTML = view.stageRail(flow.stages, flow.current_stage);
  elements.stageDetails.innerHTML = view.stageDetails(flow.stages, flow.current_stage);
  bindConfirmationActions(elements.nextActionPanel);
  for (const button of elements.stageDetails.querySelectorAll("[data-open-test-case-revision]")) {
    button.addEventListener("click", openTestCaseRevisionDialog);
  }
  for (const button of elements.stageDetails.querySelectorAll("[data-rerun-test-data]")) {
    button.addEventListener("click", () => rerunTestData(button.dataset.rerunTestData));
  }
  bindImpactGraph(flow);
  for (const button of elements.flowStages.querySelectorAll("[data-stage-target]")) {
    button.addEventListener("click", () => {
      document.getElementById(`stage-${button.dataset.stageTarget}`)?.scrollIntoView({behavior: "smooth", block: "start"});
    });
  }
}

async function rerunTestData(runId) {
  if (!state.requestId || !runId) return;
  const scope = `test-data-rerun:${state.requestId}:${runId}`;
  try {
    await api(
      `/api/v1/change-requests/${encodeURIComponent(state.requestId)}/test-data-runs/${encodeURIComponent(runId)}/rerun`,
      {method: "POST", idempotencyScope: scope, body: "{}"}
    );
    clearCommandKey(scope);
    showNotice("同じ確認済み計画でテストデータ生成と UI 検証を再実行します。", "success");
    await loadFlow();
  } catch (error) {
    showNotice(error.message, "error");
  }
}

function bindConfirmationActions(container) {
  for (const button of container.querySelectorAll("[data-confirm-checkpoint]")) {
    button.addEventListener("click", () => decideCheckpoint(
      button.dataset.confirmCheckpoint,
      button.dataset.subjectDigest,
      "confirmed"
    ));
  }
  for (const button of container.querySelectorAll("[data-reject-checkpoint]")) {
    button.addEventListener("click", () => decideCheckpoint(
      button.dataset.rejectCheckpoint,
      button.dataset.subjectDigest,
      "rejected"
    ));
  }
}

async function decideCheckpoint(checkpoint, subjectDigest, decision) {
  if (!state.requestId || !checkpoint) return;
  const note = decision === "rejected"
    ? window.prompt("差し戻し理由を入力してください")
    : null;
  if (decision === "rejected" && (!note || !note.trim())) return;
  const scope = `confirmation:${state.requestId}:${checkpoint}:${subjectDigest}:${decision}`;
  try {
    await api(
      `/api/v1/change-requests/${encodeURIComponent(state.requestId)}/confirmations/${encodeURIComponent(checkpoint)}`,
      {
        method: "POST",
        idempotencyScope: scope,
        body: JSON.stringify({decision, ...(note ? {note: note.trim()} : {})})
      }
    );
    showNotice(decision === "confirmed" ? "確認しました。次の処理を開始します。" : "差し戻しました。", "success");
    await loadFlow();
  } catch (error) {
    showNotice(error.message, "error");
  }
}

function showEmptyState() {
  state.requestId = null;
  state.flow = null;
  elements.emptyState.classList.remove("hidden");
  elements.flowWorkspace.classList.add("hidden");
  elements.pageTitle.textContent = "変更フロー";
  elements.pageDescription.textContent = "変更要件を選択してください。設計書、コード、テスト、UI 証跡を一つの流れで追跡します。";
  elements.flowStatus.textContent = "未選択";
  elements.flowStatus.className = "status-badge neutral";
  const hasProject = Boolean(state.projectId);
  elements.emptyStateTitle.textContent = hasProject ? "変更要件から始めます" : "プロジェクトを初期化します";
  elements.emptyStateDescription.textContent = hasProject
    ? "要件を登録すると、RAG による設計書特定から最終レポートまで自動で進行します。"
    : "最初にコード Workspace と設計書のローカル保存場所を登録してください。Git は必須ではありません。";
  elements.emptyNewProjectButton.classList.toggle("hidden", hasProject);
  elements.emptyNewRequestButton.classList.toggle("hidden", !hasProject);
  schedulePolling(false);
}

function bindImpactGraph(flow) {
  const codeScope = flow.stages.find(stage => stage.stage_id === "code_scope");
  const nodes = codeScope?.details?.impact_graph?.nodes;
  if (!Array.isArray(nodes)) return;
  const graph = elements.stageDetails.querySelector("[data-impact-graph]");
  if (!graph) return;
  const selectNode = index => {
    const node = nodes[index];
    if (!node) return;
    state.impactNodePath = node.path;
    for (const element of graph.querySelectorAll("[data-impact-node-index]")) {
      element.classList.toggle("selected", Number(element.dataset.impactNodeIndex) === index);
    }
    const details = graph.querySelector("[data-impact-node-details]");
    if (details) details.innerHTML = view.impactNodeDetails(node);
  };
  for (const node of graph.querySelectorAll("[data-impact-node-index]")) {
    const index = Number(node.dataset.impactNodeIndex);
    node.addEventListener("click", () => selectNode(index));
    node.addEventListener("keydown", event => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        selectNode(index);
      }
    });
  }
  const selectedIndex = Math.max(
    0,
    nodes.findIndex(node => node.path === state.impactNodePath)
  );
  selectNode(selectedIndex);
}

function openNewProjectDialog() {
  state.projectDialogMode = "create";
  elements.projectId.value = "";
  elements.projectName.value = "";
  elements.projectWorkspaceRoot.value = "";
  elements.projectDocumentRoots.value = "";
  elements.projectTestBaseUrl.value = "";
  elements.projectTargetDataDialect.value = "postgresql";
  elements.projectTargetDataAlias.value = "";
  elements.projectTargetDataDsn.value = "";
  elements.projectTargetDataBindings.value = "";
  elements.projectDataIdentityProfiles.value = "";
  state.identityProfilesLoaded = true;
  elements.projectTargetDataStatus.textContent = "必要な場合だけ Alias、接続 Secret、確認済み Binding を設定してください。";
  elements.projectId.disabled = false;
  elements.projectWorkspaceRoot.disabled = false;
  elements.projectDialogEyebrow.textContent = "NEW PROJECT";
  elements.projectDialogTitle.textContent = "新しいプロジェクト";
  setProjectFormStatus();
  setProjectSubmitting(false);
  elements.projectDialog.showModal();
  elements.projectId.focus();
}

async function openEditProjectDialog() {
  const project = state.projects.find(item => item.project_id === state.projectId);
  if (!project) return;
  state.projectDialogMode = "edit";
  elements.projectId.value = project.project_id;
  elements.projectName.value = project.name || project.project_id;
  elements.projectWorkspaceRoot.value = project.workspace_root || "";
  elements.projectDocumentRoots.value = (project.document_roots || []).join("\n");
  elements.projectTestBaseUrl.value = project.test_base_url || "";
  elements.projectTargetDataDialect.value = project.target_data_profile?.dialect || "postgresql";
  elements.projectTargetDataAlias.value = project.target_data_profile?.connection_alias || "";
  elements.projectTargetDataDsn.value = "";
  elements.projectTargetDataBindings.value = "";
  elements.projectDataIdentityProfiles.value = "";
  state.identityProfilesLoaded = false;
  elements.projectTargetDataStatus.textContent = project.target_data_profile
    ? "確認済み Binding を読み込んでいます。接続 Secret は画面へ戻しません。"
    : "必要な場合だけ Alias、接続 Secret、確認済み Binding を設定してください。";
  elements.projectId.disabled = true;
  elements.projectWorkspaceRoot.disabled = true;
  elements.projectDialogEyebrow.textContent = "PROJECT SETTINGS";
  elements.projectDialogTitle.textContent = "プロジェクト設定";
  setProjectFormStatus("Workspace は Evidence の識別子として固定されます。設計書、名称、UI URL を変更できます。");
  setProjectSubmitting(false);
  elements.projectDialog.showModal();
  elements.projectName.focus();
  if (project.target_data_profile) {
    try {
      const result = await api(`/api/v1/projects/${encodeURIComponent(project.project_id)}/target-data-profile`);
      const profile = result.profile;
      if (profile) {
        elements.projectTargetDataDialect.value = profile.dialect || "postgresql";
        elements.projectTargetDataAlias.value = profile.connection_alias || "";
        elements.projectTargetDataBindings.value = JSON.stringify(profile.bindings || [], null, 2);
        elements.projectTargetDataStatus.textContent = profile.secret_configured
          ? "接続 Secret は設定済みです。空欄のまま保存すると現在の Secret を保持します。"
          : "接続 Secret が未設定です。保存時に入力してください。";
      }
    } catch (error) {
      elements.projectTargetDataStatus.textContent = error.message;
    }
  }
  try {
    const result = await api(`/api/v1/projects/${encodeURIComponent(project.project_id)}/data-identity-profiles`);
    elements.projectDataIdentityProfiles.value = JSON.stringify(result.profiles || [], null, 2);
    state.identityProfilesLoaded = true;
  } catch (error) {
    elements.projectTargetDataStatus.textContent = error.message;
  }
}

function setProjectFormStatus(message = "", kind = "info") {
  elements.projectFormStatus.textContent = message;
  elements.projectFormStatus.className = message
    ? `request-form-status ${kind}`
    : "request-form-status hidden";
}

function setProjectSubmitting(submitting) {
  elements.projectForm.setAttribute("aria-busy", String(submitting));
  elements.submitProjectButton.disabled = submitting;
  elements.submitProjectButton.innerHTML = submitting
    ? '<span class="button-spinner" aria-hidden="true"></span><span>保存しています</span>'
    : `<span>${state.projectDialogMode === "edit" ? "保存して再スキャン" : "初期化"}</span>`;
}

async function createProject(event) {
  event.preventDefault();
  const projectId = elements.projectId.value.trim();
  const name = elements.projectName.value.trim();
  const workspaceRoot = elements.projectWorkspaceRoot.value.trim();
  const documentRoots = elements.projectDocumentRoots.value
    .split(/\r?\n/)
    .map(value => value.trim())
    .filter(Boolean);
  const testBaseUrl = elements.projectTestBaseUrl.value.trim();
  const targetDataDialect = elements.projectTargetDataDialect.value;
  const targetDataAlias = elements.projectTargetDataAlias.value.trim();
  const targetDataDsn = elements.projectTargetDataDsn.value.trim();
  const targetDataBindingsText = elements.projectTargetDataBindings.value.trim();
  const identityProfilesText = elements.projectDataIdentityProfiles.value.trim();
  let identityProfiles = [];
  if (identityProfilesText) {
    try {
      identityProfiles = JSON.parse(identityProfilesText);
    } catch (_error) {
      return setProjectFormStatus("DataIdentityProvider は JSON 配列で入力してください。", "error");
    }
    if (!Array.isArray(identityProfiles)) {
      return setProjectFormStatus("DataIdentityProvider は JSON 配列で入力してください。", "error");
    }
  }
  let targetDataBindings = null;
  if (targetDataAlias || targetDataDsn || targetDataBindingsText) {
    if (!targetDataAlias || !targetDataBindingsText) {
      return setProjectFormStatus("DB データ準備には接続 Alias と確認済み Binding が必要です。", "error");
    }
    try {
      targetDataBindings = JSON.parse(targetDataBindingsText);
    } catch (_error) {
      return setProjectFormStatus("確認済み SQL Binding は JSON 配列で入力してください。", "error");
    }
    if (!Array.isArray(targetDataBindings) || targetDataBindings.length === 0) {
      return setProjectFormStatus("確認済み SQL Binding を一件以上入力してください。", "error");
    }
  }
  if (!projectId || !name || !workspaceRoot || documentRoots.length === 0) return;
  setProjectSubmitting(true);
  setProjectFormStatus("設定を保存し、バックグラウンド Onboarding を開始します。");
  try {
    const idempotencyScope = `project:${projectId}`;
    const editing = state.projectDialogMode === "edit";
    const current = state.projects.find(item => item.project_id === projectId);
    const result = await api(editing ? `/api/v1/projects/${encodeURIComponent(projectId)}` : "/api/v1/projects", {
      method: editing ? "PATCH" : "POST",
      idempotencyScope,
      body: JSON.stringify(editing ? {
        name,
        document_roots: documentRoots,
        test_base_url: testBaseUrl || null,
        expected_revision: current?.settings_revision
      } : {
        project_id: projectId,
        name,
        workspace_root: workspaceRoot,
        document_roots: documentRoots,
        test_base_url: testBaseUrl || null
      })
    });
    if (targetDataBindings) {
      await api(`/api/v1/projects/${encodeURIComponent(projectId)}/target-data-profile`, {
        method: "PUT",
        idempotencyScope: `project-target-data:${projectId}`,
        body: JSON.stringify({
          dialect: targetDataDialect,
          connection_alias: targetDataAlias,
          ...(targetDataDsn ? {connection_dsn: targetDataDsn} : {}),
          transaction_policy: "per_binding_transaction",
          bindings: targetDataBindings
        })
      });
      clearCommandKey(`project-target-data:${projectId}`);
    }
    if (identityProfilesText || (editing && state.identityProfilesLoaded)) {
      await api(`/api/v1/projects/${encodeURIComponent(projectId)}/data-identity-profiles`, {
        method: "PUT",
        idempotencyScope: `project-data-identity:${projectId}`,
        body: JSON.stringify({profiles: identityProfiles})
      });
      clearCommandKey(`project-data-identity:${projectId}`);
    }
    elements.projectDialog.close();
    await loadProjects(result.project.project_id);
    clearCommandKey(idempotencyScope);
    const targetProject = result.target_project || {};
    const initialized = editing
      ? "設定を保存しました。設計書の再スキャンをバックグラウンドで開始します。"
      : "プロジェクトを登録しました。設計書と RAG の準備をバックグラウンドで開始します。";
    const qualityMissing = targetProject.quality_missing_signals || [];
    showNotice(
      qualityMissing.length
        ? `${initialized} 変更開始前にコード品質基線を準備してください: ${qualityMissing.join("、")}`
        : initialized,
      qualityMissing.length ? "error" : "success"
    );
  } catch (error) {
    setProjectFormStatus(error.message, "error");
  } finally {
    setProjectSubmitting(false);
  }
}

function openRequestDialog() {
  if (!state.projectId) return showNotice("先にプロジェクトを登録してください。", "error");
  const project = state.projects.find(item => item.project_id === state.projectId);
  if (project?.onboarding?.status !== "ready") {
    return showNotice("Project Onboarding と RAG が準備完了になるまで変更要件を開始できません。", "error");
  }
  elements.requestId.value = `change-${new Date().toISOString().replace(/\D/g, "").slice(0, 14)}`;
  elements.requirementText.value = "";
  elements.requestProjectName.textContent = project?.name || project?.project_id || state.projectId;
  elements.requestWorkspaceSummary.textContent = project?.workspace_root || "未設定";
  const documentRoots = project?.document_roots || [];
  elements.requestDocumentSummary.textContent = documentRoots.length
    ? `${documentRoots.length.toLocaleString("ja-JP")} フォルダー登録済み`
    : "未設定";
  elements.requestDocumentSummary.title = documentRoots.join("\n");
  setRequestFormStatus();
  setRequestSubmitting(false);
  updateRequirementCount();
  elements.requestDialog.showModal();
  elements.requirementText.focus();
}

function updateRequirementCount() {
  const length = elements.requirementText.value.length;
  elements.requirementCount.textContent = `${length.toLocaleString("ja-JP")} / 50,000`;
  elements.requirementCount.classList.toggle("near-limit", length >= 45000);
}

function setRequestFormStatus(message = "", kind = "info") {
  elements.requestFormStatus.textContent = message;
  elements.requestFormStatus.className = message
    ? `request-form-status ${kind}`
    : "request-form-status hidden";
}

function setRequestSubmitting(submitting) {
  elements.requestForm.setAttribute("aria-busy", String(submitting));
  elements.submitRequestButton.disabled = submitting;
  elements.submitRequestButton.innerHTML = submitting
    ? '<span class="button-spinner" aria-hidden="true"></span><span>送信しています</span>'
    : '<span>変更要求を送信</span><span aria-hidden="true">→</span>';
}

async function createRequest(event) {
  event.preventDefault();
  const requestId = elements.requestId.value.trim();
  const requirement = elements.requirementText.value.trim();
  if (!requestId || !requirement) return;
  setRequestSubmitting(true);
  setRequestFormStatus("変更要求を受け付けています。この画面を閉じずにお待ちください。");
  try {
    const idempotencyScope = `change-request:${state.projectId}:${requestId}`;
    await api("/api/v1/change-requests", {
      method: "POST",
      idempotencyScope,
      body: JSON.stringify({
        change_request_id: requestId,
        project_id: state.projectId,
        requirement_text: requirement
      })
    });
    await loadRequests(requestId);
    clearCommandKey(idempotencyScope);
    elements.requestDialog.close();
    showNotice("変更要求を送信しました。関連する設計書の特定を開始します。");
  } catch (error) {
    setRequestFormStatus(error.message, "error");
  } finally {
    setRequestSubmitting(false);
  }
}

function openTestCaseRevisionDialog() {
  state.revisionProposal = null;
  elements.testCaseRevisionInstruction.value = "";
  elements.testCaseRevisionPreview.innerHTML = "";
  elements.testCaseRevisionPreview.classList.add("hidden");
  elements.confirmTestCaseRevisionButton.classList.add("hidden");
  elements.testCaseRevisionDialog.showModal();
  elements.testCaseRevisionInstruction.focus();
}

async function proposeTestCaseRevision(event) {
  event.preventDefault();
  const instruction = elements.testCaseRevisionInstruction.value.trim();
  if (!instruction || !state.requestId) return;
  try {
    const idempotencyScope = `revision-proposal:${state.requestId}:${instruction}`;
    const result = await api(
      `/api/v1/change-requests/${encodeURIComponent(state.requestId)}/test-case-revisions`,
      {
        method: "POST",
        idempotencyScope,
        body: JSON.stringify({instruction})
      }
    );
    state.revisionProposal = result.proposal;
    renderTestCaseRevisionProposal(result.proposal);
    clearCommandKey(idempotencyScope);
  } catch (error) {
    showNotice(error.message, "error");
  }
}

function renderTestCaseRevisionProposal(proposal) {
  const operations = Array.isArray(proposal.operations) ? proposal.operations : [];
  const ambiguities = Array.isArray(proposal.ambiguities) ? proposal.ambiguities : [];
  const blockers = Array.isArray(proposal.blocking_reasons) ? proposal.blocking_reasons : [];
  const operationHtml = operations.map(renderRevisionOperation).join("");
  const ambiguityHtml = ambiguities.map(ambiguity => `
    <fieldset class="revision-choice">
      <legend>${view.escapeHtml(ambiguity.question)}</legend>
      ${(ambiguity.options || []).map((option, index) => `
        <label>
          <input type="radio" name="${view.escapeHtml(ambiguity.ambiguity_id)}"
            value="${view.escapeHtml(option.option_id)}" ${index === 0 ? "checked" : ""}>
          <span><strong>${view.escapeHtml(option.label)}</strong>
          ${(option.operations || []).map(renderRevisionOperation).join("")}</span>
        </label>`).join("")}
    </fieldset>`).join("");
  const blockerHtml = blockers.length
    ? `<div class="blocker-box"><strong>適用できない理由</strong><ul>${blockers.map(
      reason => `<li>${view.escapeHtml(reason)}</li>`
    ).join("")}</ul></div>`
    : "";
  elements.testCaseRevisionPreview.innerHTML = `
    <h3>適用前の差分</h3>
    ${operationHtml || ambiguityHtml ? `<div class="revision-changes">${operationHtml}${ambiguityHtml}</div>` : ""}
    ${blockerHtml}`;
  elements.testCaseRevisionPreview.classList.remove("hidden");
  elements.confirmTestCaseRevisionButton.classList.toggle(
    "hidden",
    proposal.analysis_status === "blocked"
  );
}

function renderRevisionOperation(operation) {
  return `<article class="revision-operation">
    <strong>${view.escapeHtml(operation.case_title)}</strong>
    <small>${view.escapeHtml(operation.field)} · ${view.escapeHtml(operation.action)}</small>
    <div><del>${view.escapeHtml(operation.summary_before)}</del><span aria-hidden="true">→</span>
      <ins>${view.escapeHtml(operation.summary_after)}</ins></div>
  </article>`;
}

async function confirmTestCaseRevision() {
  const proposal = state.revisionProposal;
  if (!proposal || !state.requestId) return;
  const selections = {};
  for (const ambiguity of proposal.ambiguities || []) {
    const selected = elements.testCaseRevisionPreview.querySelector(
      `input[name="${CSS.escape(ambiguity.ambiguity_id)}"]:checked`
    );
    if (!selected) return showNotice("すべての選択肢を確認してください。", "error");
    selections[ambiguity.ambiguity_id] = selected.value;
  }
  try {
    const idempotencyScope = `revision-confirm:${state.requestId}:${proposal.proposal_id}`;
    const result = await api(
      `/api/v1/change-requests/${encodeURIComponent(state.requestId)}/test-case-revisions/${encodeURIComponent(proposal.proposal_id)}/confirm`,
      {
        method: "POST",
        idempotencyScope,
        body: JSON.stringify({selections})
      }
    );
    elements.testCaseRevisionDialog.close();
    state.revisionProposal = null;
    showNotice(
      result.state === "awaiting_copilot"
        ? "確認済みです。VS Code の GitHub Copilot が UI テスト計画と Playwright 手順を再生成します。"
        : "UI テスト計画を更新しました。"
    );
    await loadFlow();
    clearCommandKey(idempotencyScope);
  } catch (error) {
    showNotice(error.message, "error");
  }
}

function schedulePolling(enabled) {
  if (state.pollTimer) window.clearTimeout(state.pollTimer);
  state.pollTimer = enabled ? window.setTimeout(() => loadFlow().catch(error => showNotice(error.message, "error")), 3000) : null;
}

function scheduleProjectPolling(enabled) {
  if (state.projectPollTimer) window.clearTimeout(state.projectPollTimer);
  state.projectPollTimer = enabled
    ? window.setTimeout(() => loadProjects(state.projectId).catch(error => showNotice(error.message, "error")), 2000)
    : null;
}

async function requestProjectOnboarding(action) {
  if (!state.projectId) return;
  const scope = `project-onboarding:${state.projectId}:${action}`;
  try {
    await api(`/api/v1/projects/${encodeURIComponent(state.projectId)}/onboarding`, {
      method: "POST",
      idempotencyScope: scope,
      body: JSON.stringify({action})
    });
    clearCommandKey(scope);
    const message = action === "reindex"
      ? "RAG 再索引を開始しました。"
      : action === "relearn"
        ? "Project 固有の設計書再学習を開始しました。"
        : "設計書の再スキャンを開始しました。";
    showNotice(message, "success");
    await loadProjects(state.projectId);
  } catch (error) {
    showNotice(error.message, "error");
  }
}

async function retryProjectOnboarding() {
  if (!state.projectId) return;
  const scope = `project-onboarding:${state.projectId}:retry`;
  try {
    await api(`/api/v1/projects/${encodeURIComponent(state.projectId)}/onboarding/retry`, {
      method: "POST",
      idempotencyScope: scope,
      body: "{}"
    });
    clearCommandKey(scope);
    await loadProjects(state.projectId);
  } catch (error) {
    showNotice(error.message, "error");
  }
}

async function showProjectPreflight() {
  if (!state.projectId) return;
  try {
    const result = await api(`/api/v1/projects/${encodeURIComponent(state.projectId)}/preflight`);
    const details = (result.capabilities || []).map(item => `${item.capability}: ${item.status}`).join("、");
    elements.projectOnboardingSummary.title = (result.document_discovery?.review_required || []).join("\n");
    showNotice(`事前確認 ${result.status}: ${details}`, result.status === "ready" ? "success" : "error");
  } catch (error) {
    showNotice(error.message, "error");
  }
}

elements.projectSelect.addEventListener("change", async () => {
  state.projectId = elements.projectSelect.value;
  state.requestId = null;
  renderProjectSummary((state.projects || []).find(project => project.project_id === state.projectId));
  await loadRequests();
});
elements.openVsCodeButton.addEventListener("click", openSelectedProjectInVsCode);
elements.projectForm.addEventListener("submit", createProject);
elements.existingTestDataForm.addEventListener("submit", registerExistingTestData);
elements.requestForm.addEventListener("submit", createRequest);
elements.requirementText.addEventListener("input", updateRequirementCount);
elements.testCaseRevisionForm.addEventListener("submit", proposeTestCaseRevision);
document.getElementById("newRequestButton").addEventListener("click", openRequestDialog);
document.getElementById("newProjectButton").addEventListener("click", openNewProjectDialog);
elements.emptyNewProjectButton.addEventListener("click", openNewProjectDialog);
elements.editProjectButton.addEventListener("click", openEditProjectDialog);
elements.existingTestDataButton.addEventListener("click", () => openExistingTestDataPage().catch(error => showNotice(error.message, "error")));
elements.fixedDataIdentifiersButton.addEventListener("click", () => openFixedDataIdentifiersPage().catch(error => showNotice(error.message, "error")));
elements.projectPreflightButton.addEventListener("click", showProjectPreflight);
elements.documentLearningButton.addEventListener("click", () => openDocumentLearningDialog().catch(error => showNotice(error.message, "error")));
elements.projectRescanButton.addEventListener("click", () => requestProjectOnboarding("rescan"));
elements.projectReindexButton.addEventListener("click", () => requestProjectOnboarding("reindex"));
elements.projectRetryButton.addEventListener("click", retryProjectOnboarding);
elements.relearnDocumentsButton.addEventListener("click", async () => {
  elements.documentLearningDialog.close();
  await requestProjectOnboarding("relearn");
});
elements.openLearningVsCodeButton.addEventListener("click", openSelectedProjectInVsCode);
elements.confirmDocumentLearningButton.addEventListener("click", confirmDocumentLearning);
elements.emptyNewRequestButton.addEventListener("click", openRequestDialog);
document.getElementById("refreshButton").addEventListener("click", () => loadRequests(state.requestId));
document.getElementById("closeDialogButton").addEventListener("click", () => elements.requestDialog.close());
document.getElementById("cancelDialogButton").addEventListener("click", () => elements.requestDialog.close());
document.getElementById("closeProjectDialogButton").addEventListener("click", () => elements.projectDialog.close());
document.getElementById("cancelProjectDialogButton").addEventListener("click", () => elements.projectDialog.close());
document.getElementById("closeExistingTestDataButton").addEventListener("click", () => elements.existingTestDataDialog.close());
document.getElementById("refreshExistingTestDataButton").addEventListener("click", () => loadExistingTestData().catch(error => showNotice(error.message, "error")));
document.getElementById("closeFixedDataIdentifiersButton").addEventListener("click", () => elements.fixedDataIdentifiersDialog.close());
document.getElementById("closeDocumentLearningButton").addEventListener("click", () => elements.documentLearningDialog.close());
document.getElementById("closeTestCaseRevisionButton").addEventListener("click", () => elements.testCaseRevisionDialog.close());
document.getElementById("cancelTestCaseRevisionButton").addEventListener("click", () => elements.testCaseRevisionDialog.close());
elements.confirmTestCaseRevisionButton.addEventListener("click", confirmTestCaseRevision);

loadProjects().catch(error => showNotice(error.message, "error"));
