"""model_validation: evaluation 领域实现与合同。"""

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


def _evaluation_evidence(
    nodes: list[Mapping[str, Any]],
) -> tuple[Mapping[str, Any], np.ndarray, list[str], np.ndarray, np.ndarray, np.ndarray, list[int]] | None:
    node = find_evidence(
        nodes,
        "decision_matrix",
        "criterion_types",
        "normalized_matrix",
        "weight_method",
        "weights",
        "scores",
        "ranking",
    )
    if node is None:
        return None
    try:
        matrix = np.asarray(node["decision_matrix"], dtype=float)
        criterion_types = list(node["criterion_types"])
        normalized = np.asarray(node["normalized_matrix"], dtype=float)
        weights = np.asarray(node["weights"], dtype=float).reshape(-1)
        scores = np.asarray(node["scores"], dtype=float).reshape(-1)
        ranking = list(node["ranking"])
    except (TypeError, ValueError):
        return None
    if (
        matrix.ndim != 2
        or matrix.shape[0] < 2
        or matrix.shape[1] < 1
        or normalized.shape != matrix.shape
        or weights.shape != (matrix.shape[1],)
        or scores.shape != (matrix.shape[0],)
        or len(criterion_types) != matrix.shape[1]
        or any(item not in {"benefit", "cost"} for item in criterion_types)
        or sorted(ranking) != list(range(matrix.shape[0]))
        or not np.all(np.isfinite(matrix))
        or not np.all(np.isfinite(normalized))
        or not np.all(np.isfinite(weights))
        or not np.all(np.isfinite(scores))
    ):
        return None
    return node, matrix, criterion_types, normalized, weights, scores, ranking

def _evaluation_normalized(matrix: np.ndarray, criterion_types: list[str]) -> np.ndarray | None:
    minima = np.min(matrix, axis=0)
    maxima = np.max(matrix, axis=0)
    spans = maxima - minima
    if np.any(spans <= 0.0):
        return None
    result = (matrix - minima) / spans
    for column, criterion_type in enumerate(criterion_types):
        if criterion_type == "cost":
            result[:, column] = 1.0 - result[:, column]
    return result

def evaluation_normalization(nodes: list[Mapping[str, Any]]) -> ValidationCheck:
    parsed = _evaluation_evidence(nodes)
    if parsed is None:
        return not_assessed("complete decision matrix and normalization evidence were not recorded")
    _, matrix, criterion_types, normalized, _, _, _ = parsed
    expected = _evaluation_normalized(matrix, criterion_types)
    if expected is None:
        return failed("at least one criterion does not discriminate between alternatives")
    if not np.allclose(normalized, expected, rtol=1e-9, atol=1e-9):
        return failed("recorded benefit/cost normalization disagrees with independent recomputation")
    return passed_check("benefit and cost criteria were independently normalized")

def evaluation_weights(nodes: list[Mapping[str, Any]]) -> ValidationCheck:
    parsed = _evaluation_evidence(nodes)
    if parsed is None:
        return not_assessed("complete decision weight evidence was not recorded")
    node, matrix, criterion_types, _, weights, _, _ = parsed
    if np.any(weights < 0.0) or not np.isclose(np.sum(weights), 1.0, rtol=1e-9, atol=1e-9):
        return failed("decision weights must be non-negative and sum to one")
    method = node["weight_method"]
    if method == "provided":
        return passed_check("provided weights are finite, non-negative, and normalized")
    if method != "entropy":
        return failed("weight method must be provided or entropy")
    normalized = _evaluation_normalized(matrix, criterion_types)
    if normalized is None:
        return failed("entropy weights require discriminating criteria")
    probabilities = normalized / np.sum(normalized, axis=0)
    positive = probabilities > 0.0
    entropy = -np.sum(
        np.where(positive, probabilities * np.log(np.where(positive, probabilities, 1.0)), 0.0),
        axis=0,
    ) / np.log(matrix.shape[0])
    expected = 1.0 - entropy
    if float(np.sum(expected)) <= 0.0:
        return failed("entropy weighting produced no discriminatory information")
    expected /= np.sum(expected)
    if not np.allclose(weights, expected, rtol=1e-9, atol=1e-9):
        return failed("recorded entropy weights disagree with independent recomputation")
    return passed_check("entropy weights were independently recomputed")

def evaluation_ranking(nodes: list[Mapping[str, Any]]) -> ValidationCheck:
    parsed = _evaluation_evidence(nodes)
    if parsed is None:
        return not_assessed("complete decision ranking evidence was not recorded")
    _, matrix, criterion_types, _, weights, scores, ranking = parsed
    normalized = _evaluation_normalized(matrix, criterion_types)
    if normalized is None:
        return failed("ranking cannot be recomputed from non-discriminating criteria")
    expected_scores = normalized @ weights
    if not np.allclose(scores, expected_scores, rtol=1e-9, atol=1e-9):
        return failed("recorded decision scores disagree with independent recomputation")
    if ranking != _rank_descending(expected_scores):
        return failed("recorded ranking disagrees with independently recomputed scores")
    return passed_check("weighted scores and deterministic ranking were independently recomputed")

def evaluation_sensitivity(nodes: list[Mapping[str, Any]]) -> ValidationCheck:
    parsed = _evaluation_evidence(nodes)
    node = find_evidence(nodes, "weight_perturbation")
    if parsed is None or node is None or not isinstance(node["weight_perturbation"], Mapping):
        return not_assessed("weight perturbation evidence was not recorded")
    _, matrix, criterion_types, _, weights, _, ranking = parsed
    evidence = node["weight_perturbation"]
    fraction = evidence.get("fraction")
    threshold = evidence.get("threshold")
    runs = evidence.get("runs")
    if (
        not finite_number(fraction)
        or not 0.0 < fraction < 1.0
        or not finite_number(threshold)
        or not 0.0 <= threshold <= 1.0
        or not isinstance(runs, list)
        or len(runs) != 2 * len(weights)
    ):
        return failed("weight perturbation design is incomplete or malformed")
    normalized = _evaluation_normalized(matrix, criterion_types)
    if normalized is None:
        return failed("weight sensitivity requires discriminating criteria")
    base_positions = np.empty(len(ranking), dtype=float)
    base_positions[ranking] = np.arange(len(ranking), dtype=float)
    correlations: list[float] = []
    expected_index = 0
    for criterion in range(len(weights)):
        for direction in (-1.0, 1.0):
            run = runs[expected_index]
            expected_index += 1
            if not isinstance(run, Mapping):
                return failed("weight perturbation run must be a mapping")
            expected_weights = weights.copy()
            expected_weights[criterion] *= 1.0 + direction * fraction
            expected_weights /= np.sum(expected_weights)
            try:
                recorded_weights = np.asarray(run["weights"], dtype=float).reshape(-1)
                recorded_ranking = list(run["ranking"])
            except (KeyError, TypeError, ValueError):
                return failed("weight perturbation run is malformed")
            expected_ranking = _rank_descending(normalized @ expected_weights)
            if not np.allclose(recorded_weights, expected_weights, rtol=1e-9, atol=1e-9):
                return failed("weight perturbation weights disagree with the declared fraction")
            if recorded_ranking != expected_ranking:
                return failed("perturbed ranking disagrees with independent recomputation")
            positions = np.empty(len(ranking), dtype=float)
            positions[recorded_ranking] = np.arange(len(ranking), dtype=float)
            correlations.append(float(np.corrcoef(base_positions, positions)[0, 1]))
    retention = sum(run["ranking"][0] == ranking[0] for run in runs) / len(runs)
    minimum_correlation = min(correlations)
    stable = retention >= threshold and minimum_correlation >= threshold
    recorded_values = {
        "top_rank_retention_rate": retention,
        "min_rank_correlation": minimum_correlation,
    }
    for name, expected in recorded_values.items():
        recorded = evidence.get(name)
        if not finite_number(recorded) or not math.isclose(recorded, expected, rel_tol=1e-9, abs_tol=1e-9):
            return failed(f"weight sensitivity metric mismatch: {name}")
    if evidence.get("stable") is not stable:
        return failed("reported ranking stability disagrees with recomputed perturbations")
    if not stable:
        return failed("ranking is not stable under the declared weight perturbations")
    return passed_check("all one-at-a-time weight perturbations and rankings were independently recomputed")

def _rank_descending(scores: np.ndarray) -> list[int]:
    return np.lexsort((np.arange(len(scores)), -scores)).astype(int).tolist()
