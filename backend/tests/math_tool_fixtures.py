"""脚本化供应商的数学工具调用。生产工作台负责参数校验与候选提交。"""
import json

from litellm import ModelResponse


def tool_reply(*actions):
    calls = [
        {"id": f"call_{i}", "type": "function",
         "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)}}
        for i, (name, args) in enumerate(actions)
    ]
    return ModelResponse(choices=[{"finish_reason": "tool_calls",
                                  "message": {"role": "assistant", "tool_calls": calls}}])


def scripted_model_reply(payload, kwargs):
    if not kwargs.get("tools"):
        content = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
        return ModelResponse(choices=[{"finish_reason": "stop",
                                      "message": {"role": "assistant", "content": content}}])
    if isinstance(payload, dict) and "inspect_operators" in payload:
        return tool_reply(("inspect_operators", {"operator_ids": payload["inspect_operators"]}))
    if isinstance(payload, dict) and "subproblems" in payload:
        draft = dict(payload)
        core = draft.pop("indexed_data", None)
        edit = ("edit_model", {"architecture": draft, **(core or {})})
        return tool_reply(edit) if core is not None else tool_reply(edit, ("submit_model", {}))
    return tool_reply(("edit_model", payload), ("submit_model", {}))
