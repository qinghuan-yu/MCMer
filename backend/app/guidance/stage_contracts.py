"""Guidance 职责边界：stage_contracts。"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from app.guidance.contracts import ProblemSpec
from app.guidance.model_ir import ModelIR
from app.guidance.model_review_contracts import ModelIRReview


class ProblemDecomposer(Protocol):
    async def decompose(
        self,
        *,
        question: str,
        evidence_profile: dict,
        input_catalog: dict,
        task: dict,
    ) -> ProblemSpec: ...

class Modeler(Protocol):
    async def model(
        self,
        *,
        problem_spec: ProblemSpec,
        evidence_profile: dict,
        task: dict,
        work_dir: Path,
    ) -> ModelIR: ...

    async def revise(
        self,
        *,
        problem_spec: ProblemSpec,
        previous_model_ir: ModelIR,
        review: ModelIRReview,
        evidence_profile: dict,
        task: dict,
        work_dir: Path,
    ) -> ModelIR: ...

class ModelReviewer(Protocol):
    async def review(
        self,
        *,
        problem_spec: ProblemSpec,
        model_ir: ModelIR,
        revision_request: ModelIRReview | None,
        evidence_profile: dict,
        task: dict,
        work_dir: Path,
    ) -> ModelIRReview: ...
