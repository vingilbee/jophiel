"""Compare rules and optional LLM predictions on reviewed cognitive cases.

From backend: python -m app.ai.Cognitive.system.training.evaluate_llm --llm
Each case makes one provider request when --llm is supplied. No agent runs.
"""

import argparse
import json
from pathlib import Path
from time import perf_counter

from app.core.config import get_settings
from ..llm import predict_state
from ..pipeline import CognitiveInputPipeline


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--llm", action="store_true", help="Also run one LLM call per case")
    parser.add_argument("--cases", type=Path, default=Path(__file__).parents[1] / "data" / "cognitive_cases.json")
    parser.add_argument("--output", type=Path, help="Save full predictions for constraint and goal review")
    args = parser.parse_args()
    cases = json.loads(args.cases.read_text())
    pipeline = CognitiveInputPipeline()
    results = []
    for case in cases:
        history = case.get("recent_messages", [])
        expected = case["expected"]
        baseline = pipeline.process(case["message"], history)
        row = {
            "id": case["id"], "message": case["message"], "recent_messages": history,
            "image_count": case.get("image_count", 0), "asset_count": case.get("asset_count", 0),
            "available_capabilities": case.get("available_capabilities", []),
            "expected": expected, "rules": baseline.model_dump(mode="json"),
            "rules_intent_correct": baseline.interpretation.intent == expected["intent"],
            "rules_clarification_correct": baseline.interpretation.needs_clarification == (expected["action"] == "clarify"),
        }
        if args.llm:
            started = perf_counter()
            try:
                state = predict_state(
                    case["message"], history,
                    image_count=case.get("image_count", 0), asset_count=case.get("asset_count", 0),
                    available_capabilities=case.get("available_capabilities", []),
                )
                row.update(
                    llm=state.model_dump(mode="json"),
                    llm_intent_correct=state.interpretation.primary_intent == expected["intent"],
                    llm_action_correct=state.action.kind == expected["action"],
                    llm_capability_correct=state.action.capability == expected.get("capability"),
                )
            except Exception as exc:
                # Continue evaluation, but count failures in every score's denominator.
                row.update(error=type(exc).__name__, llm_intent_correct=False,
                           llm_action_correct=False, llm_capability_correct=False)
            row["llm_ms"] = round((perf_counter() - started) * 1000, 2)
        results.append(row)
    fields = ["rules_intent_correct", "rules_clarification_correct"]
    if args.llm:
        fields += ["llm_intent_correct", "llm_action_correct", "llm_capability_correct"]
    report = {
        "case_count": len(results),
        "model": (get_settings().cognitive_model or get_settings().chat_model) if args.llm else None,
        "error_count": sum("error" in row for row in results),
        "scores": {field: sum(row[field] for row in results) / len(results) for field in fields} if results else {},
        "review_note": "Review goals and constraint preservation manually; these small-case scores are not general accuracy estimates.",
        "results": results,
    }
    if args.output:
        args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({k: v for k, v in report.items() if k != "results"}, indent=2))


if __name__ == "__main__":
    main()
