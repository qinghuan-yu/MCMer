"""operator_runtimes: plotting 领域实现与合同。"""

from __future__ import annotations

from pathlib import (
    Path,
    PurePosixPath,
)

import matplotlib

from app.guidance.operator_runtimes.artifact_io import workspace_path

matplotlib.use("Agg")
import matplotlib.pyplot as plt


def xy_evidence(*, inputs: dict, parameters: dict, random_seed, work_dir: Path) -> dict:
    del random_seed
    relative_path = workspace_path(parameters["path"], work_dir)
    relative_path.parent.mkdir(parents=True, exist_ok=True)
    figure, axis = plt.subplots(figsize=(8, 4.5))
    axis.plot(inputs["x"], inputs["observed"], label="observed", linewidth=2)
    axis.plot(inputs["x"], inputs["predicted"], label="fitted", linewidth=2)
    axis.set_title(str(parameters.get("title", "Observed and fitted")))
    axis.set_xlabel(str(parameters.get("x_label", "x")))
    axis.set_ylabel(str(parameters.get("y_label", "y")))
    axis.legend()
    figure.tight_layout()
    figure.savefig(relative_path, dpi=int(parameters.get("dpi", 150)))
    plt.close(figure)
    return {"figure": {
        "path": PurePosixPath(parameters["path"]).as_posix(),
        "role": str(parameters.get("role", "result_fit")),
        "linked_result_ids": list(parameters.get("linked_result_ids", [])),
    }}
