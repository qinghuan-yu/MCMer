"""model_validation: graph 领域实现与合同。"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

import numpy as np

from app.guidance.model_validation.contracts import (
    ValidationCheck,
    failed,
    find_evidence,
    finite_number,
    not_assessed,
    passed_check,
)


def _graph_evidence(
    nodes: list[Mapping[str, Any]],
) -> tuple[Mapping[str, Any], int, int, int, list[Mapping[str, Any]]] | None:
    node = find_evidence(
        nodes,
        "node_count",
        "source",
        "sink",
        "edges",
        "connectivity",
        "baseline",
        "shortest_path",
        "max_flow",
    )
    if node is None:
        return None
    node_count = node["node_count"]
    source = node["source"]
    sink = node["sink"]
    edges = node["edges"]
    if (
        not isinstance(node_count, int)
        or isinstance(node_count, bool)
        or node_count < 2
        or not isinstance(source, int)
        or not isinstance(sink, int)
        or source == sink
        or source not in range(node_count)
        or sink not in range(node_count)
        or not isinstance(edges, list)
        or not edges
        or not all(isinstance(edge, Mapping) for edge in edges)
    ):
        return None
    for edge_id, edge in enumerate(edges):
        if edge.get("id") != edge_id:
            return None
        start = edge.get("source")
        target = edge.get("target")
        weight = edge.get("weight")
        capacity = edge.get("capacity")
        if (
            not isinstance(start, int)
            or not isinstance(target, int)
            or start not in range(node_count)
            or target not in range(node_count)
            or start == target
            or not finite_number(weight)
            or not finite_number(capacity)
            or weight < 0.0
            or capacity < 0.0
        ):
            return None
    return node, node_count, source, sink, edges

def _graph_reachable_nodes(start: int, adjacency: list[list[int]]) -> list[int]:
    visited = {start}
    queue = [start]
    for node in queue:
        for neighbor in adjacency[node]:
            if neighbor not in visited:
                visited.add(neighbor)
                queue.append(neighbor)
    return sorted(visited)

def graph_baseline(nodes: list[Mapping[str, Any]]) -> ValidationCheck:
    parsed = _graph_evidence(nodes)
    if parsed is None:
        return not_assessed("complete graph baseline evidence was not recorded")
    node, _, _, _, _ = parsed
    baseline = node["baseline"]
    shortest = node["shortest_path"]
    flow = node["max_flow"]
    if not all(isinstance(item, Mapping) for item in (baseline, shortest, flow)):
        return failed("graph baseline evidence is malformed")
    baseline_distance = baseline.get("shortest_hop_distance")
    distance = shortest.get("distance")
    baseline_flow = baseline.get("flow_value")
    flow_value = flow.get("value")
    if not all(finite_number(value) for value in (
        baseline_distance,
        distance,
        baseline_flow,
        flow_value,
    )):
        return failed("graph baseline comparison requires finite path and flow values")
    if distance > baseline_distance + 1e-10:
        return failed("weighted shortest path is worse than the shortest-hop baseline")
    if flow_value < baseline_flow - 1e-10:
        return failed("maximum flow is worse than the zero-flow baseline")
    return passed_check("weighted path and feasible flow improve on declared graph baselines")

def graph_connectivity(nodes: list[Mapping[str, Any]]) -> ValidationCheck:
    parsed = _graph_evidence(nodes)
    if parsed is None:
        return not_assessed("complete graph connectivity evidence was not recorded")
    node, node_count, source, _, edges = parsed
    evidence = node["connectivity"]
    if not isinstance(evidence, Mapping):
        return failed("graph connectivity evidence must be a mapping")
    forward = [[] for _ in range(node_count)]
    reverse = [[] for _ in range(node_count)]
    weak = [[] for _ in range(node_count)]
    for edge in edges:
        start = edge["source"]
        target = edge["target"]
        forward[start].append(target)
        reverse[target].append(start)
        weak[start].append(target)
        weak[target].append(start)
    expected_reachable = _graph_reachable_nodes(source, forward)
    weakly_connected = len(_graph_reachable_nodes(0, weak)) == node_count
    strongly_connected = (
        len(_graph_reachable_nodes(0, forward)) == node_count
        and len(_graph_reachable_nodes(0, reverse)) == node_count
    )
    if evidence.get("reachable_from_source") != expected_reachable:
        return failed("source reachability disagrees with independent graph traversal")
    if evidence.get("weakly_connected") is not weakly_connected:
        return failed("weak connectivity status disagrees with independent traversal")
    if evidence.get("strongly_connected") is not strongly_connected:
        return failed("strong connectivity status disagrees with independent traversal")
    return passed_check("reachability and directed connectivity were independently recomputed")

def graph_shortest_path(nodes: list[Mapping[str, Any]]) -> ValidationCheck:
    parsed = _graph_evidence(nodes)
    if parsed is None:
        return not_assessed("complete shortest-path evidence was not recorded")
    node, node_count, source, sink, edges = parsed
    evidence = node["shortest_path"]
    if not isinstance(evidence, Mapping):
        return failed("shortest-path evidence must be a mapping")
    distances = np.full(node_count, np.inf)
    distances[source] = 0.0
    for _ in range(node_count - 1):
        changed = False
        for edge in edges:
            start = edge["source"]
            target = edge["target"]
            candidate = distances[start] + edge["weight"]
            if candidate < distances[target]:
                distances[target] = candidate
                changed = True
        if not changed:
            break
    expected_labels = [float(value) if np.isfinite(value) else None for value in distances]
    if evidence.get("distance_labels") != expected_labels:
        return failed("shortest-path distance labels disagree with Bellman-Ford recomputation")
    reachable = bool(np.isfinite(distances[sink]))
    if evidence.get("reachable") is not reachable:
        return failed("shortest-path reachability disagrees with recomputed labels")
    if not reachable:
        if evidence.get("path") or evidence.get("edge_ids") or evidence.get("distance") is not None:
            return failed("unreachable sink must not report a path or finite distance")
        return passed_check("sink unreachability was independently verified")
    path = evidence.get("path")
    edge_ids = evidence.get("edge_ids")
    distance = evidence.get("distance")
    if (
        not isinstance(path, list)
        or not isinstance(edge_ids, list)
        or len(edge_ids) != len(path) - 1
        or path[0] != source
        or path[-1] != sink
        or not finite_number(distance)
    ):
        return failed("shortest-path node and edge sequences are malformed")
    path_distance = 0.0
    for start, target, edge_id in zip(path[:-1], path[1:], edge_ids):
        if not isinstance(edge_id, int) or edge_id not in range(len(edges)):
            return failed("shortest-path edge id is invalid")
        edge = edges[edge_id]
        if edge["source"] != start or edge["target"] != target:
            return failed("shortest-path edge sequence does not match its nodes")
        path_distance += edge["weight"]
    if not math.isclose(distance, path_distance, rel_tol=1e-10, abs_tol=1e-10):
        return failed("reported shortest-path distance does not match the path edges")
    if not math.isclose(distance, distances[sink], rel_tol=1e-10, abs_tol=1e-10):
        return failed("reported path is not shortest under independent recomputation")
    return passed_check("path sequence and optimal distance were independently verified")

def _graph_flow_values(
    node: Mapping[str, Any],
    node_count: int,
    source: int,
    sink: int,
    edges: list[Mapping[str, Any]],
) -> tuple[np.ndarray, np.ndarray, float] | None:
    evidence = node["max_flow"]
    if not isinstance(evidence, Mapping):
        return None
    try:
        flows = np.asarray(evidence["edge_flows"], dtype=float).reshape(-1)
        value = float(evidence["value"])
    except (KeyError, TypeError, ValueError):
        return None
    if flows.shape != (len(edges),) or not np.all(np.isfinite(flows)) or not math.isfinite(value):
        return None
    balances = np.zeros(node_count, dtype=float)
    for edge, flow in zip(edges, flows):
        balances[edge["source"]] -= flow
        balances[edge["target"]] += flow
    if not math.isclose(-balances[source], value, rel_tol=1e-10, abs_tol=1e-10):
        return None
    if not math.isclose(balances[sink], value, rel_tol=1e-10, abs_tol=1e-10):
        return None
    return flows, balances, value

def graph_flow(nodes: list[Mapping[str, Any]]) -> ValidationCheck:
    parsed = _graph_evidence(nodes)
    if parsed is None:
        return not_assessed("complete graph flow evidence was not recorded")
    node, node_count, source, sink, edges = parsed
    flow_values = _graph_flow_values(node, node_count, source, sink, edges)
    if flow_values is None:
        return failed("graph flow evidence is malformed or inconsistent at source and sink")
    flows, balances, _ = flow_values
    capacities = np.asarray([edge["capacity"] for edge in edges])
    capacity_violation = float(max(
        np.max(-flows, initial=0.0),
        np.max(flows - capacities, initial=0.0),
        0.0,
    ))
    internal = [node_id for node_id in range(node_count) if node_id not in {source, sink}]
    conservation_violation = float(np.max(np.abs(balances[internal]), initial=0.0))
    evidence = node["max_flow"]
    for name, expected in {
        "capacity_violation": capacity_violation,
        "conservation_violation": conservation_violation,
    }.items():
        recorded = evidence.get(name)
        if not finite_number(recorded) or not math.isclose(recorded, expected, rel_tol=1e-10, abs_tol=1e-10):
            return failed(f"graph flow certificate mismatch: {name}")
    if max(capacity_violation, conservation_violation) > 1e-10:
        return failed("reported graph flow is not feasible")
    return passed_check("edge capacities and node flow conservation were independently verified")

def graph_min_cut(nodes: list[Mapping[str, Any]]) -> ValidationCheck:
    parsed = _graph_evidence(nodes)
    if parsed is None:
        return not_assessed("complete graph minimum-cut evidence was not recorded")
    node, node_count, source, sink, edges = parsed
    flow_values = _graph_flow_values(node, node_count, source, sink, edges)
    evidence = node["max_flow"]
    cut = evidence.get("min_cut") if isinstance(evidence, Mapping) else None
    if flow_values is None or not isinstance(cut, Mapping):
        return failed("minimum-cut evidence is malformed")
    _, _, flow_value = flow_values
    source_side = cut.get("source_side")
    sink_side = cut.get("sink_side")
    edge_ids = cut.get("edge_ids")
    if (
        not isinstance(source_side, list)
        or not isinstance(sink_side, list)
        or not isinstance(edge_ids, list)
        or set(source_side).union(sink_side) != set(range(node_count))
        or set(source_side).intersection(sink_side)
        or source not in source_side
        or sink not in sink_side
    ):
        return failed("minimum-cut partition is invalid")
    source_set = set(source_side)
    expected_edge_ids = [
        edge["id"]
        for edge in edges
        if edge["source"] in source_set and edge["target"] not in source_set
    ]
    capacity = sum(edges[edge_id]["capacity"] for edge_id in expected_edge_ids)
    if edge_ids != expected_edge_ids:
        return failed("minimum-cut edge ids disagree with the declared partition")
    recorded_capacity = cut.get("capacity")
    if not finite_number(recorded_capacity) or not math.isclose(recorded_capacity, capacity, rel_tol=1e-10, abs_tol=1e-10):
        return failed("minimum-cut capacity disagrees with independent recomputation")
    if not math.isclose(flow_value, capacity, rel_tol=1e-10, abs_tol=1e-10):
        return failed("max-flow value does not equal the certified cut capacity")
    return passed_check("flow-cut equality independently certifies maximum-flow optimality")
