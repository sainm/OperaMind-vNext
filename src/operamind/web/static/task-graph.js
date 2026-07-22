"use strict";

(function exposeTaskGraph(root, factory) {
  const api = factory();
  root.OperaMindTaskGraph = api;
  if (typeof module === "object" && module.exports) module.exports = api;
})(typeof globalThis === "object" ? globalThis : window, function createTaskGraphApi() {
  const BLOCKING_STATES = new Set(["blocked", "failed"]);
  const PROPAGATION_TERMINALS = new Set(["completed", "cancelled", "superseded"]);
  const NODE_WIDTH = 196;
  const NODE_HEIGHT = 80;
  const LAYER_GAP = 78;
  const ROW_GAP = 30;
  const MARGIN_X = 42;
  const MARGIN_Y = 34;

  function buildTaskDependencyGraph(tasks) {
    const nodesById = new Map();
    for (const task of tasks || []) {
      const id = task && task.orchestration_task_id;
      if (typeof id !== "string" || !id || nodesById.has(id)) continue;
      nodesById.set(id, normalizeNode(task));
    }
    for (const node of [...nodesById.values()]) {
      for (const dependencyId of node.dependencies) {
        if (!nodesById.has(dependencyId)) {
          nodesById.set(dependencyId, normalizeExternalNode(dependencyId, node));
        }
      }
    }

    const nodes = [...nodesById.values()].sort(compareNodes);
    const outgoing = new Map(nodes.map(node => [node.id, []]));
    const incoming = new Map(nodes.map(node => [node.id, []]));
    const edges = [];
    for (const node of nodes) {
      for (const dependencyId of node.dependencies) {
        if (!nodesById.has(dependencyId)) continue;
        const edge = {from: dependencyId, to: node.id, critical: false, blocking: false, cycle: false};
        edges.push(edge);
        outgoing.get(dependencyId).push(node.id);
        incoming.get(node.id).push(dependencyId);
      }
    }
    for (const values of outgoing.values()) values.sort((a, b) => compareNodes(nodesById.get(a), nodesById.get(b)));
    for (const values of incoming.values()) values.sort((a, b) => compareNodes(nodesById.get(a), nodesById.get(b)));

    const indegree = new Map(nodes.map(node => [node.id, incoming.get(node.id).length]));
    const layers = new Map(nodes.map(node => [node.id, 0]));
    const distance = new Map(nodes.map(node => [node.id, 1]));
    const previous = new Map();
    const queue = nodes.filter(node => indegree.get(node.id) === 0).sort(compareNodes);
    const visited = [];
    while (queue.length) {
      const node = queue.shift();
      visited.push(node.id);
      for (const childId of outgoing.get(node.id)) {
        layers.set(childId, Math.max(layers.get(childId), layers.get(node.id) + 1));
        const candidateDistance = distance.get(node.id) + 1;
        const existingPrevious = previous.get(childId);
        if (
          candidateDistance > distance.get(childId) ||
          (candidateDistance === distance.get(childId) &&
            (!existingPrevious || compareNodes(node, nodesById.get(existingPrevious)) < 0))
        ) {
          distance.set(childId, candidateDistance);
          previous.set(childId, node.id);
        }
        indegree.set(childId, indegree.get(childId) - 1);
        if (indegree.get(childId) === 0) {
          queue.push(nodesById.get(childId));
          queue.sort(compareNodes);
        }
      }
    }

    const cycleNodeIds = nodes.filter(node => indegree.get(node.id) > 0).map(node => node.id);
    const maximumLayer = Math.max(0, ...layers.values());
    for (const id of cycleNodeIds) layers.set(id, maximumLayer + 1);
    const cycleSet = new Set(cycleNodeIds);
    for (const edge of edges) edge.cycle = cycleSet.has(edge.from) && cycleSet.has(edge.to);

    const criticalPath = longestPath(visited, distance, previous, nodesById);
    const criticalNodes = new Set(criticalPath);
    const criticalEdges = new Set(
      criticalPath.slice(1).map((id, index) => `${criticalPath[index]}\u0000${id}`),
    );
    const blockedBy = propagateBlocking(nodes, nodesById, outgoing);
    for (const edge of edges) {
      edge.critical = criticalEdges.has(`${edge.from}\u0000${edge.to}`);
      const upstream = new Set([edge.from, ...(blockedBy.get(edge.from) || [])]);
      edge.blocking = [...(blockedBy.get(edge.to) || [])].some(id => upstream.has(id));
    }

    const byLayer = new Map();
    for (const node of nodes) {
      const layer = layers.get(node.id);
      if (!byLayer.has(layer)) byLayer.set(layer, []);
      byLayer.get(layer).push(node);
    }
    let maximumRows = 1;
    for (const layerNodes of byLayer.values()) {
      layerNodes.sort(compareNodes);
      maximumRows = Math.max(maximumRows, layerNodes.length);
      layerNodes.forEach((node, row) => {
        node.layer = layers.get(node.id);
        node.row = row;
        node.x = MARGIN_X + node.layer * (NODE_WIDTH + LAYER_GAP);
        node.y = MARGIN_Y + row * (NODE_HEIGHT + ROW_GAP);
        node.width = NODE_WIDTH;
        node.height = NODE_HEIGHT;
        node.critical = criticalNodes.has(node.id);
        node.cycle = cycleSet.has(node.id);
        node.blockedBy = [...(blockedBy.get(node.id) || [])].sort();
        node.predecessorCount = incoming.get(node.id).length;
      });
    }
    const layerCount = Math.max(1, ...nodes.map(node => node.layer + 1));
    return {
      nodes,
      edges,
      criticalPath,
      cycleNodeIds,
      blockedPropagationCount: nodes.filter(node => node.blockedBy.length > 0).length,
      width: MARGIN_X * 2 + layerCount * NODE_WIDTH + Math.max(0, layerCount - 1) * LAYER_GAP,
      height: MARGIN_Y * 2 + maximumRows * NODE_HEIGHT + Math.max(0, maximumRows - 1) * ROW_GAP,
    };
  }

  function normalizeNode(task) {
    return {
      id: task.orchestration_task_id,
      sequence: Number.isInteger(task.sequence) ? task.sequence : 0,
      title: typeof task.title === "string" && task.title ? task.title : task.orchestration_task_id,
      state: task.effective_state || task.state || "unknown",
      storedState: task.state || "unknown",
      blockingReason: typeof task.blocking_reason === "string" ? task.blocking_reason : null,
      dependencies: [...new Set(Array.isArray(task.dependencies) ? task.dependencies.filter(value => typeof value === "string" && value) : [])].sort(),
      external: false,
      automationRunId: task.automation_run_id || null,
    };
  }

  function normalizeExternalNode(id, child) {
    return {
      id,
      sequence: -1,
      title: id,
      state: "unknown",
      storedState: "unknown",
      blockingReason: null,
      dependencies: [],
      external: true,
      automationRunId: child.automationRunId,
    };
  }

  function compareNodes(left, right) {
    return (left.sequence - right.sequence) || left.id.localeCompare(right.id);
  }

  function longestPath(visited, distance, previous, nodesById) {
    if (!visited.length) return [];
    let endpoint = visited[0];
    for (const id of visited.slice(1)) {
      if (
        distance.get(id) > distance.get(endpoint) ||
        (distance.get(id) === distance.get(endpoint) &&
          compareNodes(nodesById.get(id), nodesById.get(endpoint)) < 0)
      ) endpoint = id;
    }
    const path = [];
    for (let current = endpoint; current; current = previous.get(current)) path.unshift(current);
    return path;
  }

  function propagateBlocking(nodes, nodesById, outgoing) {
    const blockedBy = new Map(nodes.map(node => [node.id, new Set()]));
    for (const source of nodes.filter(node => BLOCKING_STATES.has(node.storedState))) {
      const pending = [...outgoing.get(source.id)];
      const visited = new Set();
      while (pending.length) {
        const id = pending.shift();
        if (visited.has(id)) continue;
        visited.add(id);
        const node = nodesById.get(id);
        if (!node || PROPAGATION_TERMINALS.has(node.storedState)) continue;
        blockedBy.get(id).add(source.id);
        pending.push(...outgoing.get(id));
      }
    }
    return blockedBy;
  }

  function edgePath(edge, nodesById) {
    const source = nodesById.get(edge.from);
    const target = nodesById.get(edge.to);
    if (!source || !target) return "";
    const startX = source.x + source.width;
    const startY = source.y + source.height / 2;
    const endX = target.x;
    const endY = target.y + target.height / 2;
    if (endX > startX) {
      const middle = startX + (endX - startX) / 2;
      return `M ${startX} ${startY} C ${middle} ${startY}, ${middle} ${endY}, ${endX} ${endY}`;
    }
    const bendY = Math.max(12, Math.min(startY, endY) - 24);
    return `M ${startX} ${startY} C ${startX + 28} ${bendY}, ${endX - 28} ${bendY}, ${endX} ${endY}`;
  }

  return Object.freeze({buildTaskDependencyGraph, edgePath});
});
