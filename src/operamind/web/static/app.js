"use strict";

const view = window.OperaMindChangeFlow;
const state = {
  projects: [],
  projectId: null,
  requestId: null,
  flow: null,
  pollTimer: null,
  revisionProposal: null,
  commandKeys: new Map()
};

const elements = {
  projectSelect: document.getElementById("projectSelect"),
  requestList: document.getElementById("requestList"),
  notice: document.getElementById("notice"),
  emptyState: document.getElementById("emptyState"),
  flowWorkspace: document.getElementById("flowWorkspace"),
  pageTitle: document.getElementById("pageTitle"),
  pageDescription: document.getElementById("pageDescription"),
  flowStatus: document.getElementById("flowStatus"),
  progressValue: document.getElementById("progressValue"),
  progressBar: document.getElementById("progressBar"),
  flowStages: document.getElementById("flowStages"),
  stageDetails: document.getElementById("stageDetails"),
  projectSummary: document.getElementById("projectSummary"),
  projectSourceKind: document.getElementById("projectSourceKind"),
  projectWorkspaceSummary: document.getElementById("projectWorkspaceSummary"),
  projectDocumentRootSummary: document.getElementById("projectDocumentRootSummary"),
  emptyStateTitle: document.getElementById("emptyStateTitle"),
  emptyStateDescription: document.getElementById("emptyStateDescription"),
  emptyNewProjectButton: document.getElementById("emptyNewProjectButton"),
  emptyNewRequestButton: document.getElementById("emptyNewRequestButton"),
  projectDialog: document.getElementById("projectDialog"),
  projectForm: document.getElementById("projectForm"),
  projectId: document.getElementById("projectId"),
  projectName: document.getElementById("projectName"),
  projectWorkspaceRoot: document.getElementById("projectWorkspaceRoot"),
  projectDocumentRoots: document.getElementById("projectDocumentRoots"),
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
  testCaseRevisionPanel: document.getElementById("testCaseRevisionPanel"),
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
  if (!state.projectId) {
    elements.requestList.innerHTML = '<p class="empty">登録済みプロジェクトがありません。</p>';
    showEmptyState();
    return;
  }
  await loadRequests();
}

function renderProjectSummary(project) {
  elements.projectSummary.classList.toggle("hidden", !project?.workspace_root);
  if (!project?.workspace_root) return;
  elements.projectSourceKind.textContent = project.source_control_kind === "git" ? "Git" : "ローカルファイル";
  elements.projectWorkspaceSummary.textContent = project.workspace_root;
  elements.projectDocumentRootSummary.innerHTML = (project.document_roots || []).map(root =>
    `<li>${view.escapeHtml(root)}</li>`
  ).join("");
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
  elements.pageTitle.textContent = flow.change_request_id;
  const requirement = flow.stages.find(stage => stage.stage_id === "requirement");
  elements.pageDescription.textContent = requirement?.details?.requirement_text || requirement?.summary || "変更フロー";
  elements.flowStatus.textContent = view.statusLabels[flow.status] || flow.status;
  elements.flowStatus.className = `status-badge ${flow.status}`;
  elements.progressValue.textContent = `${flow.progress_percent}%`;
  elements.progressBar.style.width = `${flow.progress_percent}%`;
  elements.flowStages.innerHTML = view.stageRail(flow.stages, flow.current_stage);
  elements.stageDetails.innerHTML = view.stageDetails(flow.stages);
  const compileStage = flow.stages.find(stage => stage.stage_id === "compile_test");
  const testCases = compileStage?.details?.test_cases;
  elements.testCaseRevisionPanel.classList.toggle(
    "hidden",
    !Array.isArray(testCases) || testCases.length === 0
  );
  for (const button of elements.flowStages.querySelectorAll("[data-stage-target]")) {
    button.addEventListener("click", () => {
      document.getElementById(`stage-${button.dataset.stageTarget}`)?.scrollIntoView({behavior: "smooth", block: "start"});
    });
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
  elements.testCaseRevisionPanel.classList.add("hidden");
  const hasProject = Boolean(state.projectId);
  elements.emptyStateTitle.textContent = hasProject ? "変更要件から始めます" : "プロジェクトを初期化します";
  elements.emptyStateDescription.textContent = hasProject
    ? "要件を登録すると、RAG による設計書特定から最終レポートまで自動で進行します。"
    : "最初にコード Workspace と設計書のローカル保存場所を登録してください。Git は必須ではありません。";
  elements.emptyNewProjectButton.classList.toggle("hidden", hasProject);
  elements.emptyNewRequestButton.classList.toggle("hidden", !hasProject);
  schedulePolling(false);
}

function openProjectDialog() {
  elements.projectId.value = "";
  elements.projectName.value = "";
  elements.projectWorkspaceRoot.value = "";
  elements.projectDocumentRoots.value = "";
  elements.projectDialog.showModal();
  elements.projectId.focus();
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
  if (!projectId || !name || !workspaceRoot || documentRoots.length === 0) return;
  try {
    const idempotencyScope = `project:${projectId}`;
    const result = await api("/api/v1/projects", {
      method: "POST",
      idempotencyScope,
      body: JSON.stringify({
        project_id: projectId,
        name,
        workspace_root: workspaceRoot,
        document_roots: documentRoots
      })
    });
    elements.projectDialog.close();
    await loadProjects(result.project.project_id);
    clearCommandKey(idempotencyScope);
    showNotice("プロジェクトを初期化しました。");
  } catch (error) {
    showNotice(error.message, "error");
  }
}

function openRequestDialog() {
  if (!state.projectId) return showNotice("先にプロジェクトを登録してください。", "error");
  const project = state.projects.find(item => item.project_id === state.projectId);
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
    await api(
      `/api/v1/change-requests/${encodeURIComponent(state.requestId)}/test-case-revisions/${encodeURIComponent(proposal.proposal_id)}/confirm`,
      {
        method: "POST",
        idempotencyScope,
        body: JSON.stringify({selections})
      }
    );
    elements.testCaseRevisionDialog.close();
    state.revisionProposal = null;
    showNotice("テストケースを更新し、下流のテスト計画を再生成しました。");
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

elements.projectSelect.addEventListener("change", async () => {
  state.projectId = elements.projectSelect.value;
  state.requestId = null;
  renderProjectSummary((state.projects || []).find(project => project.project_id === state.projectId));
  await loadRequests();
});
elements.projectForm.addEventListener("submit", createProject);
elements.requestForm.addEventListener("submit", createRequest);
elements.requirementText.addEventListener("input", updateRequirementCount);
elements.testCaseRevisionForm.addEventListener("submit", proposeTestCaseRevision);
document.getElementById("newRequestButton").addEventListener("click", openRequestDialog);
document.getElementById("newProjectButton").addEventListener("click", openProjectDialog);
elements.emptyNewProjectButton.addEventListener("click", openProjectDialog);
elements.emptyNewRequestButton.addEventListener("click", openRequestDialog);
document.getElementById("openTestCaseRevisionButton").addEventListener("click", openTestCaseRevisionDialog);
document.getElementById("refreshButton").addEventListener("click", () => loadRequests(state.requestId));
document.getElementById("closeDialogButton").addEventListener("click", () => elements.requestDialog.close());
document.getElementById("cancelDialogButton").addEventListener("click", () => elements.requestDialog.close());
document.getElementById("closeProjectDialogButton").addEventListener("click", () => elements.projectDialog.close());
document.getElementById("cancelProjectDialogButton").addEventListener("click", () => elements.projectDialog.close());
document.getElementById("closeTestCaseRevisionButton").addEventListener("click", () => elements.testCaseRevisionDialog.close());
document.getElementById("cancelTestCaseRevisionButton").addEventListener("click", () => elements.testCaseRevisionDialog.close());
elements.confirmTestCaseRevisionButton.addEventListener("click", confirmTestCaseRevision);

loadProjects().catch(error => showNotice(error.message, "error"));
