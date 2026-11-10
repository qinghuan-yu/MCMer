"""operator_runtimes: graph 领域实现与合同。"""

from __future__ import annotations

import heapq
import math
from collections import deque
from pathlib import Path

import numpy as np


def _graph_reachable(
    start: int,
    adjacency: list[list[int]],
) -> list[int]:
    visited = {start}
    queue = deque([start])
    while queue:
        node = queue.popleft()
        for neighbor in adjacency[node]:
            if neighbor not in visited:
                visited.add(neighbor)
                queue.append(neighbor)
    return sorted(visited)

def shortest_path_max_flow(
    *,
    inputs: dict,
    parameters: dict,
    random_seed,
    work_dir: Path,
) -> dict:
    del random_seed, work_dir
    metrics = np.asarray(inputs["metrics"], dtype=float)
    endpoints = np.asarray(parameters["endpoints"], dtype=float)
    if (
        metrics.ndim != 2
        or metrics.shape[1] != 2
        or len(metrics) < 1
        or endpoints.shape != metrics.shape
        or not np.all(np.isfinite(metrics))
        or not np.all(np.isfinite(endpoints))
    ):
        raise ValueError(
            "graph metrics and endpoints must be aligned finite matrices with two columns"
        )
    raw_edges = np.column_stack([endpoints, metrics])
    node_count = parameters["node_count"]
    source = parameters["source"]
    sink = parameters["sink"]
    if (
        not isinstance(node_count, int)
        or isinstance(node_count, bool)
        or node_count < 2
        or not isinstance(source, int)
        or isinstance(source, bool)
        or not isinstance(sink, int)
        or isinstance(sink, bool)
        or source == sink
        or source not in range(node_count)
        or sink not in range(node_count)
    ):
        raise ValueError("graph node_count, source, and sink are invalid")
    endpoints = raw_edges[:, :2]
    if (
        not np.all(endpoints == np.floor(endpoints))
        or np.any(endpoints < 0)
        or np.any(endpoints >= node_count)
        or np.any(endpoints[:, 0] == endpoints[:, 1])
        or np.any(raw_edges[:, 2:] < 0.0)
    ):
        raise ValueError("graph edges contain invalid endpoints, weights, or capacities")
    starts = endpoints[:, 0].astype(int)
    targets = endpoints[:, 1].astype(int)
    weights = raw_edges[:, 2]
    capacities = raw_edges[:, 3]

    forward: list[list[tuple[int, float, int]]] = [[] for _ in range(node_count)]
    reverse: list[list[int]] = [[] for _ in range(node_count)]
    weak: list[list[int]] = [[] for _ in range(node_count)]
    for edge_id, (start, target, weight) in enumerate(zip(starts, targets, weights)):
        start_node = int(start)
        target_node = int(target)
        forward[start_node].append((target_node, float(weight), edge_id))
        reverse[target_node].append(start_node)
        weak[start_node].append(target_node)
        weak[target_node].append(start_node)

    distances = np.full(node_count, np.inf)
    distances[source] = 0.0
    predecessor_node = np.full(node_count, -1, dtype=int)
    predecessor_edge = np.full(node_count, -1, dtype=int)
    queue: list[tuple[float, int]] = [(0.0, source)]
    while queue:
        distance, node = heapq.heappop(queue)
        if distance > distances[node]:
            continue
        for neighbor, weight, edge_id in forward[node]:
            candidate = distance + weight
            if candidate < distances[neighbor]:
                distances[neighbor] = candidate
                predecessor_node[neighbor] = node
                predecessor_edge[neighbor] = edge_id
                heapq.heappush(queue, (candidate, neighbor))
    if np.isfinite(distances[sink]):
        path = [sink]
        path_edges: list[int] = []
        current = sink
        while current != source:
            path_edges.append(int(predecessor_edge[current]))
            current = int(predecessor_node[current])
            path.append(current)
        path.reverse()
        path_edges.reverse()
        shortest_distance: float | None = float(distances[sink])
    else:
        path = []
        path_edges = []
        shortest_distance = None

    hop_predecessor = np.full(node_count, -1, dtype=int)
    hop_edge = np.full(node_count, -1, dtype=int)
    hop_seen = {source}
    hop_queue = deque([source])
    while hop_queue and sink not in hop_seen:
        node = hop_queue.popleft()
        for neighbor, _, edge_id in forward[node]:
            if neighbor not in hop_seen:
                hop_seen.add(neighbor)
                hop_predecessor[neighbor] = node
                hop_edge[neighbor] = edge_id
                hop_queue.append(neighbor)
    if sink in hop_seen:
        baseline_edges: list[int] = []
        current = sink
        while current != source:
            baseline_edges.append(int(hop_edge[current]))
            current = int(hop_predecessor[current])
        baseline_distance: float | None = float(np.sum(weights[baseline_edges]))
    else:
        baseline_distance = None

    flows = np.zeros(len(raw_edges), dtype=float)

    def residual_arcs(node: int):
        for edge_id, (start, target, capacity) in enumerate(zip(starts, targets, capacities)):
            if start == node and capacity - flows[edge_id] > 1e-12:
                yield int(target), edge_id, 1, float(capacity - flows[edge_id])
            if target == node and flows[edge_id] > 1e-12:
                yield int(start), edge_id, -1, float(flows[edge_id])

    while True:
        parent: dict[int, tuple[int, int, int, float]] = {}
        seen = {source}
        flow_queue = deque([source])
        while flow_queue and sink not in seen:
            node = flow_queue.popleft()
            for neighbor, edge_id, direction, residual_capacity in residual_arcs(node):
                if neighbor not in seen:
                    seen.add(neighbor)
                    parent[neighbor] = (node, edge_id, direction, residual_capacity)
                    flow_queue.append(neighbor)
        if sink not in seen:
            break
        augmentation = float("inf")
        current = sink
        while current != source:
            previous, _, _, residual_capacity = parent[current]
            augmentation = min(augmentation, residual_capacity)
            current = previous
        current = sink
        while current != source:
            previous, edge_id, direction, _ = parent[current]
            flows[edge_id] += direction * augmentation
            current = previous

    residual_adjacency: list[list[int]] = [[] for _ in range(node_count)]
    for node in range(node_count):
        residual_adjacency[node] = [neighbor for neighbor, _, _, _ in residual_arcs(node)]
    source_side = _graph_reachable(source, residual_adjacency)
    source_set = set(source_side)
    sink_side = [node for node in range(node_count) if node not in source_set]
    cut_edge_ids = [
        edge_id
        for edge_id, (start, target) in enumerate(zip(starts, targets))
        if start in source_set and target not in source_set
    ]
    cut_capacity = float(np.sum(capacities[cut_edge_ids]))
    balances = np.zeros(node_count, dtype=float)
    for start, target, flow in zip(starts, targets, flows):
        balances[start] -= flow
        balances[target] += flow
    flow_value = float(-balances[source])
    internal = [node for node in range(node_count) if node not in {source, sink}]
    conservation_violation = float(np.max(np.abs(balances[internal]), initial=0.0))
    capacity_violation = float(max(
        np.max(-flows, initial=0.0),
        np.max(flows - capacities, initial=0.0),
        0.0,
    ))
    reachable_from_source = _graph_reachable(
        source,
        [[neighbor for neighbor, _, _ in items] for items in forward],
    )
    forward_plain = [[neighbor for neighbor, _, _ in items] for items in forward]
    strongly_connected = (
        len(_graph_reachable(0, forward_plain)) == node_count
        and len(_graph_reachable(0, reverse)) == node_count
    )
    edges = [
        {
            "id": edge_id,
            "source": int(start),
            "target": int(target),
            "weight": float(weight),
            "capacity": float(capacity),
        }
        for edge_id, (start, target, weight, capacity) in enumerate(
            zip(starts, targets, weights, capacities)
        )
    ]
    return {"analysis": {
        "node_count": node_count,
        "source": source,
        "sink": sink,
        "edges": edges,
        "connectivity": {
            "reachable_from_source": reachable_from_source,
            "weakly_connected": len(_graph_reachable(0, weak)) == node_count,
            "strongly_connected": strongly_connected,
        },
        "baseline": {
            "shortest_hop_distance": baseline_distance,
            "flow_value": 0.0,
        },
        "shortest_path": {
            "reachable": shortest_distance is not None,
            "path": path,
            "edge_ids": path_edges,
            "distance": shortest_distance,
            "distance_labels": [float(value) if np.isfinite(value) else None for value in distances],
        },
        "max_flow": {
            "value": flow_value,
            "edge_flows": flows.tolist(),
            "capacity_violation": capacity_violation,
            "conservation_violation": conservation_violation,
            "min_cut": {
                "source_side": source_side,
                "sink_side": sink_side,
                "edge_ids": cut_edge_ids,
                "capacity": cut_capacity,
            },
        },
        "extreme_inputs": {
            "finite": bool(np.all(np.isfinite(flows))),
            "constraints_satisfied": bool(
                capacity_violation <= 1e-10
                and conservation_violation <= 1e-10
                and math.isclose(flow_value, cut_capacity, rel_tol=1e-10, abs_tol=1e-10)
            ),
        },
    }}
