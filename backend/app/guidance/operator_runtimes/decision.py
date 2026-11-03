"""operator_runtimes: decision 领域实现与合同。"""

from __future__ import annotations

from pathlib import Path

import numpy as np


def _mcda_normalize(
    matrix: np.ndarray,
    criterion_types: list[str],
) -> np.ndarray:
    if matrix.ndim != 2 or matrix.shape[0] < 2 or matrix.shape[1] < 1:
        raise ValueError("decision matrix must contain at least two alternatives")
    if not np.all(np.isfinite(matrix)):
        raise ValueError("decision matrix must contain only finite values")
    if len(criterion_types) != matrix.shape[1] or any(
        item not in {"benefit", "cost"} for item in criterion_types
    ):
        raise ValueError("criterion_types must declare benefit or cost for every column")
    minima = np.min(matrix, axis=0)
    maxima = np.max(matrix, axis=0)
    spans = maxima - minima
    if np.any(spans <= 0.0):
        raise ValueError("every criterion must discriminate between alternatives")
    normalized = (matrix - minima) / spans
    for column, criterion_type in enumerate(criterion_types):
        if criterion_type == "cost":
            normalized[:, column] = 1.0 - normalized[:, column]
    return normalized

def _mcda_ranking(scores: np.ndarray) -> list[int]:
    alternative_ids = np.arange(len(scores))
    return np.lexsort((alternative_ids, -scores)).astype(int).tolist()

def mcda_weighted_ranking(
    *,
    inputs: dict,
    parameters: dict,
    random_seed,
    work_dir: Path,
) -> dict:
    del random_seed, work_dir
    matrix = np.asarray(inputs["matrix"], dtype=float)
    criterion_types = list(parameters["criterion_types"])
    normalized = _mcda_normalize(matrix, criterion_types)
    method = parameters["weight_method"]
    if method == "provided":
        weights = np.asarray(parameters.get("weights", []), dtype=float).reshape(-1)
    elif method == "entropy":
        probabilities = normalized / np.sum(normalized, axis=0)
        positive = probabilities > 0.0
        entropy = -np.sum(
            np.where(positive, probabilities * np.log(np.where(positive, probabilities, 1.0)), 0.0),
            axis=0,
        ) / np.log(matrix.shape[0])
        weights = 1.0 - entropy
    else:
        raise ValueError("weight_method must be provided or entropy")
    if (
        weights.shape != (matrix.shape[1],)
        or not np.all(np.isfinite(weights))
        or np.any(weights < 0.0)
        or float(np.sum(weights)) <= 0.0
    ):
        raise ValueError("weights must be finite, non-negative, and match the criteria")
    weights = weights / np.sum(weights)
    scores = normalized @ weights
    ranking = _mcda_ranking(scores)

    fraction = float(parameters.get("perturbation_fraction", 0.05))
    stability_threshold = float(parameters.get("stability_threshold", 0.8))
    if not 0.0 < fraction < 1.0:
        raise ValueError("perturbation_fraction must be between zero and one")
    if not 0.0 <= stability_threshold <= 1.0:
        raise ValueError("stability_threshold must be between zero and one")
    base_positions = np.empty(len(ranking), dtype=float)
    base_positions[ranking] = np.arange(len(ranking), dtype=float)
    runs: list[dict] = []
    correlations: list[float] = []
    for criterion in range(len(weights)):
        for direction in (-1.0, 1.0):
            perturbed = weights.copy()
            perturbed[criterion] *= 1.0 + direction * fraction
            perturbed /= np.sum(perturbed)
            perturbed_ranking = _mcda_ranking(normalized @ perturbed)
            positions = np.empty(len(ranking), dtype=float)
            positions[perturbed_ranking] = np.arange(len(ranking), dtype=float)
            correlations.append(float(np.corrcoef(base_positions, positions)[0, 1]))
            runs.append({
                "weights": perturbed.tolist(),
                "ranking": perturbed_ranking,
            })
    top_retention = sum(run["ranking"][0] == ranking[0] for run in runs) / len(runs)
    minimum_correlation = min(correlations)
    stable = top_retention >= stability_threshold and minimum_correlation >= stability_threshold
    return {"decision": {
        "decision_matrix": matrix.tolist(),
        "criterion_types": criterion_types,
        "normalized_matrix": normalized.tolist(),
        "weight_method": method,
        "weights": weights.tolist(),
        "scores": scores.tolist(),
        "ranking": ranking,
        "weight_perturbation": {
            "fraction": fraction,
            "runs": runs,
            "top_rank_retention_rate": top_retention,
            "min_rank_correlation": minimum_correlation,
            "threshold": stability_threshold,
            "stable": stable,
        },
        "extreme_inputs": {
            "finite": bool(np.all(np.isfinite(scores))),
            "constraints_satisfied": bool(
                np.all(weights >= 0.0) and np.isclose(np.sum(weights), 1.0)
            ),
        },
    }}
