"use strict";

(function exposeTraceabilityView(root, factory) {
  const api = factory();
  root.OperaMindTraceabilityView = api;
  if (typeof module === "object" && module.exports) module.exports = api;
})(typeof globalThis === "object" ? globalThis : window, function createTraceabilityViewApi() {
  const STAGE_LABELS = Object.freeze({
    "変更要件": "変更要件",
    "設計変更": "設計変更",
    "影響項目": "影響分析",
    "影響コード": "影響コード",
    "業務ルール": "業務ルール",
    "検証基準": "検証基準",
    "Test Case": "テストケース",
    "Case 修正": "テストケース修正履歴",
    "テストデータ": "テストデータ",
    "UI Scenario": "UI シナリオ",
    "UI 検証結果": "UI テスト結果",
    "業務カバレッジ": "業務カバレッジ",
    "Copilot 変更タスク": "VS Code 変更作業",
    "コード変更結果": "コード変更結果",
    "Closure Result": "変更完了判定",
  });
  const NODE_WIDTH = 190;
  const NODE_HEIGHT = 76;
  const COLUMN_GAP = 70;
  const ROW_GAP = 26;
  const MARGIN = 34;

  function buildViewModel(traceability) {
    const nodes = traceability && Array.isArray(traceability.nodes) ? traceability.nodes : [];
    const nodeLabels = Object.fromEntries(nodes.map(node => [node.id, node.title || node.id]));
    const stages = (traceability?.summary?.stage_order || Object.keys(STAGE_LABELS))
      .map(kind => ({
        kind,
        label: STAGE_LABELS[kind] || kind,
        nodes: nodes.filter(node => node.kind === kind),
      }))
      .filter(stage => stage.nodes.length);
    const gaps = Array.isArray(traceability?.gaps) ? traceability.gaps.map(gap => ({
      ...gap,
      label: gap.severity === "critical" ? "必須工程の欠落" : "確認が必要",
    })) : [];
    return Object.freeze({
      stages,
      gaps,
      edges: Array.isArray(traceability?.edges) ? traceability.edges.map(edge => ({
        ...edge,
        from_label: nodeLabels[edge.from] || edge.from,
        to_label: nodeLabels[edge.to] || edge.to,
      })) : [],
      summary: traceability?.summary || {node_count: 0, edge_count: 0, gap_count: gaps.length, critical_gap_count: 0},
    });
  }

  function buildGraph(traceability) {
    const view = buildViewModel(traceability);
    const stageIndex = new Map(view.stages.map((stage, index) => [stage.kind, index]));
    const criticalGapNodes = new Set(
      view.gaps.filter(gap => gap.severity === "critical" && gap.node_id).map(gap => gap.node_id),
    );
    const nodes = view.stages.flatMap(stage => stage.nodes.map(node => ({
      ...node,
      stageLabel: stage.label,
      layer: stageIndex.get(stage.kind) || 0,
      blocked: criticalGapNodes.has(node.id),
    })));
    const nodesById = new Map(nodes.map(node => [node.id, node]));
    const edges = view.edges.filter(edge => nodesById.has(edge.from) && nodesById.has(edge.to))
      .map(edge => ({...edge, blocking: criticalGapNodes.has(edge.to), critical: false}));
    const incoming = new Map(nodes.map(node => [node.id, []]));
    const outgoing = new Map(nodes.map(node => [node.id, []]));
    for (const edge of edges) {
      incoming.get(edge.to).push(edge.from);
      outgoing.get(edge.from).push(edge.to);
    }
    const distance = new Map(nodes.map(node => [node.id, 1]));
    const previous = new Map();
    for (const node of [...nodes].sort((a, b) => a.layer - b.layer || a.id.localeCompare(b.id))) {
      for (const child of outgoing.get(node.id)) {
        if (distance.get(node.id) + 1 > distance.get(child)) {
          distance.set(child, distance.get(node.id) + 1);
          previous.set(child, node.id);
        }
      }
    }
    const endpoint = [...nodes].sort((a, b) => distance.get(b.id) - distance.get(a.id) || a.id.localeCompare(b.id))[0];
    const criticalPath = [];
    for (let id = endpoint?.id; id; id = previous.get(id)) criticalPath.unshift(id);
    const criticalEdges = new Set(criticalPath.slice(1).map((id, index) => `${criticalPath[index]}\u0000${id}`));
    const blockedBy = propagate(criticalGapNodes, outgoing);
    for (const edge of edges) {
      edge.critical = criticalEdges.has(`${edge.from}\u0000${edge.to}`);
      const upstreamSources = new Set(blockedBy.get(edge.from) || []);
      if (criticalGapNodes.has(edge.from)) upstreamSources.add(edge.from);
      edge.blocking = edge.blocking || [...(blockedBy.get(edge.to) || [])]
        .some(source => upstreamSources.has(source));
    }

    const byLayer = new Map();
    for (const node of nodes) {
      if (!byLayer.has(node.layer)) byLayer.set(node.layer, []);
      byLayer.get(node.layer).push(node);
    }
    let maxRows = 1;
    for (const [layer, values] of byLayer) {
      values.sort((a, b) => a.id.localeCompare(b.id));
      maxRows = Math.max(maxRows, values.length);
      values.forEach((node, row) => Object.assign(node, {
        x: MARGIN + layer * (NODE_WIDTH + COLUMN_GAP),
        y: MARGIN + row * (NODE_HEIGHT + ROW_GAP),
        width: NODE_WIDTH,
        height: NODE_HEIGHT,
        critical: criticalPath.includes(node.id),
        blockedBy: [...(blockedBy.get(node.id) || [])],
      }));
    }
    const columns = Math.max(1, view.stages.length);
    return {
      ...view, nodes, edges, criticalPath,
      blockingChain: nodes.filter(node => node.blocked || node.blockedBy.length).map(node => node.id),
      width: MARGIN * 2 + columns * NODE_WIDTH + Math.max(0, columns - 1) * COLUMN_GAP,
      height: MARGIN * 2 + maxRows * NODE_HEIGHT + Math.max(0, maxRows - 1) * ROW_GAP,
    };
  }

  function propagate(sources, outgoing) {
    const result = new Map();
    for (const source of sources) {
      const pending = [...(outgoing.get(source) || [])];
      const seen = new Set();
      while (pending.length) {
        const id = pending.shift();
        if (seen.has(id)) continue;
        seen.add(id);
        if (!result.has(id)) result.set(id, new Set());
        result.get(id).add(source);
        pending.push(...(outgoing.get(id) || []));
      }
    }
    return result;
  }

  function edgePath(edge, nodesById) {
    const source = nodesById.get(edge.from);
    const target = nodesById.get(edge.to);
    if (!source || !target) return "";
    const x1 = source.x + source.width;
    const y1 = source.y + source.height / 2;
    const x2 = target.x;
    const y2 = target.y + target.height / 2;
    const middle = x1 + (x2 - x1) / 2;
    return `M ${x1} ${y1} C ${middle} ${y1}, ${middle} ${y2}, ${x2} ${y2}`;
  }

  return Object.freeze({
    buildViewModel,
    buildGraph,
    edgePath,
    stageLabel: value => STAGE_LABELS[value] || value,
  });
});
