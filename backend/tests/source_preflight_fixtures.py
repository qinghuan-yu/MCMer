"""业务测试显式建立前置许可；有歧义时必须由用例提供决定与复核。"""

import json

from app.guidance.source_resolution import SourceAdjudicationResult, SourceInterpretationResult, resolve_problem_sources


def write_source_preflight(root, spec, *, catalog=None, profile=None, interpretation=None, adjudication=None):
    root.mkdir(parents=True, exist_ok=True)
    catalog_path, profile_path = root / "input_catalog.json", root / "evidence_profile.json"
    if catalog is None and catalog_path.is_file():
        catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    if profile is None:
        profile = json.loads(profile_path.read_text(encoding="utf-8")) if profile_path.is_file() else {"evidence": []}
    if interpretation is None:
        if spec.uncertainties:
            raise ValueError("test must explicitly provide decisions for its source uncertainties")
        interpretation = SourceInterpretationResult(decisions=[])
    if adjudication is None:
        if interpretation.decisions:
            raise ValueError("test must explicitly provide independent source checks")
        adjudication = SourceAdjudicationResult(checks=[])
    resolved, record = resolve_problem_sources(
        spec, interpretation=interpretation, adjudication=adjudication,
        input_catalog=catalog, evidence_profile=profile, work_dir=root,
    )
    documents = {
        "problem_spec_original.json": spec.model_dump(mode="json"),
        "problem_spec.json": resolved.model_dump(mode="json"),
        "source_preflight.json": record.model_dump(mode="json"),
        "evidence_profile.json": profile,
    }
    if catalog is not None:
        documents["input_catalog.json"] = catalog
    for name, document in documents.items():
        (root / name).write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8", newline="\n")
    return resolved, record


def bind_approved_source(root, approved, *, problem_spec=None, review=None, model_ir=None):
    """计算阶段测试显式建立来源绑定；保留原审核状态，不制造数学检查。"""
    from app.artifacts.evidence_profile import EvidenceProfile
    from app.guidance.contracts import ProblemSpec
    from app.guidance.model_review_contracts import ModelIRReview
    from app.guidance.model_review_validation import bind_model_review, load_review_catalog
    from app.guidance.recovery import load_source_preflight

    model_ir = model_ir if model_ir is not None else approved.approved_model_ir
    if problem_spec is None:
        problem_path = root / "problem_spec.json"
        problem_spec = ProblemSpec.model_validate_json(problem_path.read_text(encoding="utf-8")) if problem_path.is_file() else ProblemSpec(
            task_summary="Execute the explicitly approved model projection.",
            subproblems=[{"id": item.subproblem_id, "required_fact_ids": [],
                          "required_uncertainty_ids": [], "depends_on_subproblem_ids": []}
                         for item in model_ir.subproblems],
        )
    if (root / "source_preflight.json").is_file():
        permission = load_source_preflight(root, problem_spec)
    else:
        problem_spec, permission = write_source_preflight(root, problem_spec)
    if review is None:
        review = ModelIRReview(model_id=model_ir.model_id, subproblem_reviews=[{
            "subproblem_id": item.subproblem_id,
            "status": "approved" if item.execution_status == "solvable" else "blocked",
            "blocking_issues": [] if item.execution_status == "solvable" else [item.blocking_reason],
        } for item in model_ir.subproblems])
    (root / "model_ir.json").write_text(model_ir.model_dump_json(indent=2), encoding="utf-8", newline="\n")
    (root / "model_review.json").write_text(review.model_dump_json(indent=2), encoding="utf-8", newline="\n")
    profile = EvidenceProfile.model_validate_json((root / "evidence_profile.json").read_text(encoding="utf-8")).model_dump(mode="json")
    binding = bind_model_review(
        review=review, model_ir=model_ir, approved_model_ir=approved.approved_model_ir,
        problem_spec=problem_spec, evidence_profile=profile, input_catalog=load_review_catalog(root),
        revision_request=None, source_preflight=permission,
    )
    return approved.model_copy(update={"review_binding": binding})
