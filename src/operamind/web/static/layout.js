"use strict";

(function initializeLayoutModule(root) {
  const VIEW_DEFINITIONS = Object.freeze({
    change: {
      eyebrow: "統制された変更フロー",
      title: "変更フロー",
      description: "要件から証跡まで、判断が必要な地点だけを一段階ずつ確認します。",
      panels: [
        "workbenchOverviewPanel",
        "requestPanel",
        "automationPanel",
        "diffPanel",
        "impactPanel",
        "orchestrationPanel",
        "grantPanel",
        "copilotBridgePanel",
        "progressPanel",
      ],
    },
    tests: {
      eyebrow: "テストと検証",
      title: "テスト",
      description: "テストデータ、実行結果、カバレッジとクローズ判定をまとめて確認します。",
      panels: [
        "workbenchOverviewPanel",
        "orchestrationPanel",
        "testDataManagementPanel",
        "failureManagementPanel",
        "closureManagementPanel",
      ],
    },
    evidence: {
      eyebrow: "追跡可能な証跡",
      title: "証跡",
      description: "変更の根拠、UI 情報、未解決項目と準備状況を追跡します。",
      panels: [
        "workbenchOverviewPanel",
        "traceabilityPanel",
        "codeGraphPanel",
        "uiKnowledgePanel",
        "unresolvedEvidencePanel",
        "evidenceReadinessPanel",
      ],
    },
    operations: {
      eyebrow: "自動編成の運用",
      title: "運用",
      description: "作業、実行担当、依存関係とローカル実行環境を監視・管理します。",
      panels: ["orchestrationTaskManagementPanel", "environmentDiagnosticsPanel"],
    },
    settings: {
      eyebrow: "標準設定",
      title: "設定",
      description: "標準設定プロファイル、適用設定と設定差異の再構築状態を管理します。",
      panels: ["profileRegistryPanel"],
    },
  });

  const ALL_PANEL_IDS = Object.freeze(
    [...new Set(Object.values(VIEW_DEFINITIONS).flatMap((definition) => definition.panels))],
  );

  let activeView = "change";
  let pendingRequests = 0;
  let lastDrawerTrigger = null;

  function viewDefinition(view) {
    return VIEW_DEFINITIONS[view] || VIEW_DEFINITIONS.change;
  }

  function activePanelIds(view) {
    return [...viewDefinition(view).panels];
  }

  function activateView(view, options = {}) {
    if (!Object.prototype.hasOwnProperty.call(VIEW_DEFINITIONS, view)) view = "change";
    activeView = view;
    if (typeof document === "undefined") return viewDefinition(view);

    const definition = viewDefinition(view);
    document.body.dataset.activeView = view;
    for (const panelId of ALL_PANEL_IDS) {
      const panel = document.getElementById(panelId);
      if (panel) panel.classList.toggle("view-filtered", !definition.panels.includes(panelId));
    }
    for (const button of document.querySelectorAll("[data-view]")) {
      const selected = button.dataset.view === view;
      button.classList.toggle("active", selected);
      if (selected) button.setAttribute("aria-current", "page");
      else button.removeAttribute("aria-current");
    }

    document.getElementById("workspaceEyebrow").textContent = definition.eyebrow;
    document.getElementById("workspaceTitle").textContent = definition.title;
    document.getElementById("workspaceDescription").textContent = definition.description;
    document.title = `${definition.title} | OperaMind`;
    refreshDrawerContext();
    closeMobileNavigation();

    if (options.focus !== false) {
      root.scrollTo?.({ top: 0, behavior: options.immediate ? "auto" : "smooth" });
      document.getElementById("workspaceTitle").focus({ preventScroll: true });
    }
    return definition;
  }

  function refreshDrawerContext() {
    if (typeof document === "undefined") return;
    const context = document.getElementById("drawerContext");
    if (!context) return;
    const project = document.getElementById("projectSelect");
    const projectLabel = project?.selectedOptions?.[0]?.textContent || "未選択";
    const requestLabel = document.getElementById("currentRequest")?.textContent || "未選択";
    const definition = viewDefinition(activeView);
    context.replaceChildren(
      contextRow("ワークスペース", definition.title),
      contextRow("プロジェクト", projectLabel),
      contextRow("変更要件", requestLabel),
    );
  }

  function contextRow(label, value) {
    const row = document.createElement("div");
    row.className = "drawer-context-row";
    const name = document.createElement("span");
    name.textContent = label;
    const content = document.createElement("strong");
    content.textContent = value;
    row.append(name, content);
    return row;
  }

  function openDrawer(title = "詳細", trigger = null) {
    if (typeof document === "undefined") return;
    const drawer = document.getElementById("detailDrawer");
    lastDrawerTrigger = trigger || document.activeElement;
    document.getElementById("detailDrawerTitle").textContent = title;
    drawer.setAttribute("aria-hidden", "false");
    document.getElementById("detailDrawerToggle").setAttribute("aria-expanded", "true");
    document.querySelector(".topbar").inert = true;
    document.querySelector(".shell").inert = true;
    document.body.classList.add("drawer-open");
    refreshDrawerContext();
    root.setTimeout(
      () => document.getElementById("detailDrawerClose").focus({ preventScroll: true }),
      60,
    );
  }

  function closeDrawer(options = {}) {
    if (typeof document === "undefined") return;
    const drawer = document.getElementById("detailDrawer");
    if (drawer.getAttribute("aria-hidden") === "true") return;
    drawer.setAttribute("aria-hidden", "true");
    document.getElementById("detailDrawerToggle").setAttribute("aria-expanded", "false");
    document.querySelector(".topbar").inert = false;
    document.querySelector(".shell").inert = false;
    document.body.classList.remove("drawer-open");
    if (options.restoreFocus !== false && lastDrawerTrigger?.focus) lastDrawerTrigger.focus();
  }

  function openMobileNavigation() {
    if (typeof document === "undefined") return;
    document.body.classList.add("nav-open");
    document.getElementById("mobileNavToggle").setAttribute("aria-expanded", "true");
    syncMobileNavigationAccessibility();
    document.querySelector(".nav-item.active")?.focus();
  }

  function closeMobileNavigation(options = {}) {
    if (typeof document === "undefined") return;
    const wasOpen = document.body.classList.contains("nav-open");
    document.body.classList.remove("nav-open");
    document.getElementById("mobileNavToggle").setAttribute("aria-expanded", "false");
    syncMobileNavigationAccessibility();
    if (wasOpen && options.restoreFocus) document.getElementById("mobileNavToggle").focus();
  }

  function syncMobileNavigationAccessibility() {
    if (typeof document === "undefined") return;
    const mobile = root.matchMedia?.("(max-width: 1000px)").matches ?? false;
    const open = mobile && document.body.classList.contains("nav-open");
    const sidebar = document.getElementById("primarySidebar");
    sidebar.inert = mobile && !open;
    if (mobile && !open) sidebar.setAttribute("aria-hidden", "true");
    else sidebar.removeAttribute("aria-hidden");
    document.getElementById("workspaceMain").inert = open;
    document.getElementById("mobileNavBackdrop").setAttribute("aria-hidden", String(!open));
  }

  function setBusy(busy) {
    if (busy) pendingRequests += 1;
    else pendingRequests = Math.max(0, pendingRequests - 1);
    if (typeof document === "undefined") return pendingRequests;
    const active = pendingRequests > 0;
    const bar = document.getElementById("loadingBar");
    const main = document.getElementById("workspaceMain");
    bar.classList.toggle("active", active);
    bar.setAttribute("aria-hidden", String(!active));
    main.setAttribute("aria-busy", String(active));
    return pendingRequests;
  }

  function trapDrawerFocus(event) {
    if (event.key !== "Tab" || !document.body.classList.contains("drawer-open")) return;
    const drawer = document.getElementById("detailDrawer");
    const focusable = [...drawer.querySelectorAll(
      'button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [href], [tabindex]:not([tabindex="-1"])',
    )].filter((node) => node.offsetParent !== null);
    if (!focusable.length) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  }

  function initialize() {
    for (const button of document.querySelectorAll("[data-view]")) {
      button.addEventListener("click", () => activateView(button.dataset.view));
    }
    const drawerToggle = document.getElementById("detailDrawerToggle");
    drawerToggle.addEventListener("click", () => {
      if (document.body.classList.contains("drawer-open")) closeDrawer();
      else openDrawer("コンテキスト", drawerToggle);
    });
    document.getElementById("workspaceDetailButton").addEventListener(
      "click",
      (event) => openDrawer("コンテキスト", event.currentTarget),
    );
    document.getElementById("detailDrawerClose").addEventListener("click", () => closeDrawer());
    document.getElementById("drawerBackdrop").addEventListener("click", () => closeDrawer());
    document.getElementById("mobileNavToggle").addEventListener("click", () => {
      if (document.body.classList.contains("nav-open")) closeMobileNavigation({ restoreFocus: true });
      else openMobileNavigation();
    });
    document.getElementById("mobileNavBackdrop").addEventListener(
      "click",
      () => closeMobileNavigation({ restoreFocus: true }),
    );
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && document.body.classList.contains("drawer-open")) {
        closeDrawer();
      } else if (event.key === "Escape" && document.body.classList.contains("nav-open")) {
        closeMobileNavigation({ restoreFocus: true });
      } else {
        trapDrawerFocus(event);
      }
    });
    document.getElementById("projectSelect").addEventListener("change", refreshDrawerContext);
    root.matchMedia?.("(max-width: 1000px)").addEventListener(
      "change",
      syncMobileNavigationAccessibility,
    );
    const selected = sessionStorage.getItem("operamind.activeView");
    activateView(Object.prototype.hasOwnProperty.call(VIEW_DEFINITIONS, selected) ? selected : "change", {
      focus: false,
      immediate: true,
    });
    for (const button of document.querySelectorAll("[data-view]")) {
      button.addEventListener("click", () => sessionStorage.setItem("operamind.activeView", button.dataset.view));
    }
    syncMobileNavigationAccessibility();
  }

  const api = Object.freeze({
    VIEW_DEFINITIONS,
    activePanelIds,
    activateView,
    closeDrawer,
    closeMobileNavigation,
    openDrawer,
    openMobileNavigation,
    refreshDrawerContext,
    setBusy,
    viewDefinition,
  });
  root.OperaMindLayout = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  if (typeof document !== "undefined") initialize();
})(typeof window !== "undefined" ? window : globalThis);
