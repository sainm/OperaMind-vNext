"use strict";

(function exposeGraphCanvas(root, factory) {
  const api = factory();
  root.OperaMindGraphCanvas = api;
  if (typeof module === "object" && module.exports) module.exports = api;
})(typeof globalThis === "object" ? globalThis : window, function createGraphCanvasApi() {
  const MIN_SCALE = 0.35;
  const MAX_SCALE = 2.5;

  function clampScale(value) {
    return Math.min(MAX_SCALE, Math.max(MIN_SCALE, Number(value) || 1));
  }

  function fitTransform(viewportWidth, viewportHeight, graphWidth, graphHeight) {
    const width = Math.max(1, graphWidth);
    const height = Math.max(1, graphHeight);
    const scale = clampScale(Math.min((viewportWidth - 32) / width, (viewportHeight - 32) / height));
    return {
      x: Math.max(16, (viewportWidth - width * scale) / 2),
      y: Math.max(16, (viewportHeight - height * scale) / 2),
      scale,
    };
  }

  function attach({viewport, svg, stage, controls, width, height}) {
    if (!viewport || !svg || !stage) return null;
    viewport._operaMindGraphCanvas?.destroy?.();
    const controller = new AbortController();
    const listener = {signal: controller.signal};
    let transform = fitTransform(viewport.clientWidth || 800, viewport.clientHeight || 420, width, height);
    let dragging = null;
    const scaleOutput = controls?.querySelector("[data-graph-scale]");

    function apply() {
      stage.setAttribute("transform", `translate(${transform.x} ${transform.y}) scale(${transform.scale})`);
      if (scaleOutput) scaleOutput.textContent = `${Math.round(transform.scale * 100)}%`;
    }
    function zoom(multiplier, centerX, centerY) {
      const next = clampScale(transform.scale * multiplier);
      const ratio = next / transform.scale;
      const cx = centerX ?? (viewport.clientWidth || 800) / 2;
      const cy = centerY ?? (viewport.clientHeight || 420) / 2;
      transform = {
        x: cx - (cx - transform.x) * ratio,
        y: cy - (cy - transform.y) * ratio,
        scale: next,
      };
      apply();
    }
    function fit() {
      transform = fitTransform(viewport.clientWidth || 800, viewport.clientHeight || 420, width, height);
      apply();
    }

    svg.setAttribute("viewBox", `0 0 ${viewport.clientWidth || 800} ${viewport.clientHeight || 420}`);
    svg.setAttribute("preserveAspectRatio", "none");
    controls?.querySelector("[data-graph-action='zoom-in']")?.addEventListener("click", () => zoom(1.2), listener);
    controls?.querySelector("[data-graph-action='zoom-out']")?.addEventListener("click", () => zoom(1 / 1.2), listener);
    controls?.querySelector("[data-graph-action='fit']")?.addEventListener("click", fit, listener);
    viewport.addEventListener("wheel", event => {
      event.preventDefault();
      const bounds = viewport.getBoundingClientRect();
      zoom(event.deltaY < 0 ? 1.12 : 1 / 1.12, event.clientX - bounds.left, event.clientY - bounds.top);
    }, {passive: false, signal: controller.signal});
    viewport.addEventListener("pointerdown", event => {
      if (event.button !== 0 || event.target.closest?.("[data-graph-node]")) return;
      dragging = {x: event.clientX, y: event.clientY, originX: transform.x, originY: transform.y};
      viewport.setPointerCapture?.(event.pointerId);
      viewport.classList.add("is-panning");
    }, listener);
    viewport.addEventListener("pointermove", event => {
      if (!dragging) return;
      transform = {...transform, x: dragging.originX + event.clientX - dragging.x, y: dragging.originY + event.clientY - dragging.y};
      apply();
    }, listener);
    const stop = () => { dragging = null; viewport.classList.remove("is-panning"); };
    viewport.addEventListener("pointerup", stop, listener);
    viewport.addEventListener("pointercancel", stop, listener);
    viewport.addEventListener("keydown", event => {
      if (event.key === "+" || event.key === "=") zoom(1.2);
      else if (event.key === "-") zoom(1 / 1.2);
      else if (event.key === "0") fit();
      else return;
      event.preventDefault();
    }, listener);
    apply();
    const api = Object.freeze({
      fit,
      zoom,
      getTransform: () => ({...transform}),
      destroy: () => controller.abort(),
    });
    viewport._operaMindGraphCanvas = api;
    return api;
  }

  return Object.freeze({attach, clampScale, fitTransform, MIN_SCALE, MAX_SCALE});
});
