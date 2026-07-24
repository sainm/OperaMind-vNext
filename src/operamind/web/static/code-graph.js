"use strict";

(function exposeCodeGraph(root, factory) {
  const api = factory();
  root.OperaMindCodeGraph = api;
  if (typeof module === "object" && module.exports) module.exports = api;
})(typeof globalThis === "object" ? globalThis : window, function createCodeGraphApi() {
  const NODE_WIDTH = 210;
  const NODE_HEIGHT = 78;
  const X_GAP = 82;
  const Y_GAP = 28;
  const MARGIN = 34;

  function buildGraph(view) {
    const nodes = (view?.nodes || []).map(node => ({...node}));
    const nodesById = new Map(nodes.map(node => [node.id, node]));
    const edges = (view?.edges || [])
      .filter(edge => nodesById.has(edge.from) && nodesById.has(edge.to))
      .map(edge => ({...edge, blocking: edge.resolution !== "resolved", critical: false}));
    const incoming = new Map(nodes.map(node => [node.id, []]));
    const outgoing = new Map(nodes.map(node => [node.id, []]));
    for (const edge of edges) {
      incoming.get(edge.to).push(edge.from);
      outgoing.get(edge.from).push(edge.to);
    }
    const indegree = new Map(nodes.map(node => [node.id, incoming.get(node.id).length]));
    const layer = new Map(nodes.map(node => [node.id, 0]));
    const distance = new Map(nodes.map(node => [node.id, 1]));
    const previous = new Map();
    const queue = nodes.filter(node => indegree.get(node.id) === 0).map(node => node.id).sort();
    const visited = [];
    while (queue.length) {
      const id = queue.shift();
      visited.push(id);
      for (const child of outgoing.get(id)) {
        layer.set(child, Math.max(layer.get(child), layer.get(id) + 1));
        if (distance.get(id) + 1 > distance.get(child)) {
          distance.set(child, distance.get(id) + 1);
          previous.set(child, id);
        }
        indegree.set(child, indegree.get(child) - 1);
        if (indegree.get(child) === 0) {
          queue.push(child);
          queue.sort();
        }
      }
    }
    const cycleIds = nodes.filter(node => indegree.get(node.id) > 0).map(node => node.id);
    const lastLayer = Math.max(0, ...layer.values());
    for (const id of cycleIds) layer.set(id, lastLayer + 1);
    const endpoint = visited.sort((left, right) => distance.get(right) - distance.get(left) || left.localeCompare(right))[0];
    const criticalPath = [];
    for (let id = endpoint; id; id = previous.get(id)) criticalPath.unshift(id);
    const criticalEdges = new Set(criticalPath.slice(1).map((id, index) => `${criticalPath[index]}\u0000${id}`));
    const blockingSources = new Set(edges.filter(edge => edge.blocking).map(edge => edge.to));
    const blockedBy = propagate(blockingSources, outgoing);
    for (const edge of edges) {
      edge.critical = criticalEdges.has(`${edge.from}\u0000${edge.to}`);
      const upstreamSources = new Set(blockedBy.get(edge.from) || []);
      if (blockingSources.has(edge.from)) upstreamSources.add(edge.from);
      edge.blocking = edge.blocking || [...(blockedBy.get(edge.to) || [])]
        .some(source => upstreamSources.has(source));
    }

    const rows = new Map();
    for (const node of nodes) {
      const value = layer.get(node.id);
      if (!rows.has(value)) rows.set(value, []);
      rows.get(value).push(node);
    }
    let maxRows = 1;
    for (const [column, values] of rows) {
      values.sort((a, b) => a.id.localeCompare(b.id));
      maxRows = Math.max(maxRows, values.length);
      values.forEach((node, row) => Object.assign(node, {
        x: MARGIN + column * (NODE_WIDTH + X_GAP),
        y: MARGIN + row * (NODE_HEIGHT + Y_GAP),
        width: NODE_WIDTH,
        height: NODE_HEIGHT,
        critical: criticalPath.includes(node.id),
        blocked: blockingSources.has(node.id),
        blockedBy: [...(blockedBy.get(node.id) || [])],
        cycle: cycleIds.includes(node.id),
      }));
    }
    const columns = Math.max(1, ...nodes.map(node => layer.get(node.id) + 1));
    return {
      nodes, edges, criticalPath, cycleIds,
      blockingChain: nodes.filter(node => node.blocked || node.blockedBy.length).map(node => node.id),
      width: MARGIN * 2 + columns * NODE_WIDTH + Math.max(0, columns - 1) * X_GAP,
      height: MARGIN * 2 + maxRows * NODE_HEIGHT + Math.max(0, maxRows - 1) * Y_GAP,
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

  return Object.freeze({buildGraph, edgePath});
});
