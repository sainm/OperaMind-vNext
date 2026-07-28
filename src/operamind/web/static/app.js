"use strict";

const view = window.OperaMindChangeFlow;
const state = {
  projectId: null,
  requestId: null,
  flow: null,
  pollTimer: null,
  revisionProposal: null
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
  requestDialog: document.getElementById("requestDialog"),
  requestForm: document.getElementById("requestForm"),
  requestId: document.getElementById("requestId"),
  requirementText: document.getElementById("requirementText"),
  testCaseRevisionPanel: document.getElementById("testCaseRevisionPanel"),
  testCaseRevisionDialog: document.getElementById("testCaseRevisionDialog"),
  testCaseRevisionForm: document.getElementById("testCaseRevisionForm"),
  testCaseRevisionInstruction: document.getElementById("testCaseRevisionInstruction"),
  testCaseRevisionPreview: document.getElementById("testCaseRevisionPreview"),
  confirmTestCaseRevisionButton: document.getElementById("confirmTestCaseRevisionButton")
};

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      "X-OperaMind-Actor": "local-user",
      ...(options.method && options.method !== "GET" ? {"Idempotency-Key": crypto.randomUUID()} : {}),
      ...(options.headers || {})
    }
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.message || body.details?.reason || `HTTP ${response.status}`);
  return body;
}

function showNotice(message, kind = "info") {
  elements.notice.textContent = message;
  elements.notice.className = `notice ${kind}`;
  window.setTimeout(() => elements.notice.classList.add("hidden"), 5000);
}

async function loadProjects() {
  const result = await api("/api/v1/projects");
  elements.projectSelect.innerHTML = (result.projects || []).map(project =>
    `<option value="${view.escapeHtml(project.project_id)}">${view.escapeHtml(project.name || project.project_id)}</option>`
  ).join("");
  state.projectId = elements.projectSelect.value || null;
  if (!state.projectId) {
    elements.requestList.innerHTML = '<p class="empty">登録済みプロジェクトがありません。</p>';
    return;
  }
  await loadRequests();
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
  schedulePolling(false);
}

function openRequestDialog() {
  if (!state.projectId) return showNotice("先にプロジェクトを登録してください。", "error");
  elements.requestId.value = `change-${new Date().toISOString().replace(/\D/g, "").slice(0, 14)}`;
  elements.requirementText.value = "";
  elements.requestDialog.showModal();
  elements.requirementText.focus();
}

async function createRequest(event) {
  event.preventDefault();
  const requestId = elements.requestId.value.trim();
  const requirement = elements.requirementText.value.trim();
  if (!requestId || !requirement) return;
  try {
    await api("/api/v1/change-requests", {
      method: "POST",
      body: JSON.stringify({
        change_request_id: requestId,
        project_id: state.projectId,
        requirement_text: requirement
      })
    });
    elements.requestDialog.close();
    await loadRequests(requestId);
  } catch (error) {
    showNotice(error.message, "error");
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
    const result = await api(
      `/api/v1/change-requests/${encodeURIComponent(state.requestId)}/test-case-revisions`,
      {method: "POST", body: JSON.stringify({instruction})}
    );
    state.revisionProposal = result.proposal;
    renderTestCaseRevisionProposal(result.proposal);
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
    await api(
      `/api/v1/change-requests/${encodeURIComponent(state.requestId)}/test-case-revisions/${encodeURIComponent(proposal.proposal_id)}/confirm`,
      {method: "POST", body: JSON.stringify({selections})}
    );
    elements.testCaseRevisionDialog.close();
    state.revisionProposal = null;
    showNotice("テストケースを更新し、下流のテスト計画を再生成しました。");
    await loadFlow();
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
  await loadRequests();
});
elements.requestForm.addEventListener("submit", createRequest);
elements.testCaseRevisionForm.addEventListener("submit", proposeTestCaseRevision);
document.getElementById("newRequestButton").addEventListener("click", openRequestDialog);
document.getElementById("emptyNewRequestButton").addEventListener("click", openRequestDialog);
document.getElementById("openTestCaseRevisionButton").addEventListener("click", openTestCaseRevisionDialog);
document.getElementById("refreshButton").addEventListener("click", () => loadRequests(state.requestId));
document.getElementById("closeDialogButton").addEventListener("click", () => elements.requestDialog.close());
document.getElementById("cancelDialogButton").addEventListener("click", () => elements.requestDialog.close());
document.getElementById("closeTestCaseRevisionButton").addEventListener("click", () => elements.testCaseRevisionDialog.close());
document.getElementById("cancelTestCaseRevisionButton").addEventListener("click", () => elements.testCaseRevisionDialog.close());
elements.confirmTestCaseRevisionButton.addEventListener("click", confirmTestCaseRevision);

loadProjects().catch(error => showNotice(error.message, "error"));
