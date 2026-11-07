"""Model-free reproduction of the current tentative-retention gates. Run from repo root."""
import ast
import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "investigation_lessons", ROOT / "backend/app/ai/learning/lessons.py"
)
lessons = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = lessons
spec.loader.exec_module(lessons)

# Use the real pure admission function without importing database/service dependencies.
tree = ast.parse((ROOT / "backend/app/services/learning_lessons.py").read_text())
node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "worth_reading")
namespace = {}
exec(compile(ast.Module(body=[node], type_ignores=[]), "<actual worth_reading>", "exec"), namespace)

for status in ("plausible", "supported"):
    admission = namespace["worth_reading"]({
        "review": {"decision": "examine"},
        "findings": [{"status": status, "relates_to": [], "cites": ["g1"]}],
    })
    bundle = lessons.Bundle("assessment", 1, "occasion", datetime.now(timezone.utc), "observation", "")
    bundle.conclusions["a1"] = lessons.Conclusion(
        "a1", "finding", status, "A condition may affect an observed outcome.", ("experience:1",)
    )
    checked = lessons.check({"changes": [{
        "op": "create", "kind": "understanding", "scope": "situation",
        "statement": "A condition may affect the outcome.",
        "applies_when": "When these conditions recur", "certainty": "tentative",
        "conclusion_cites": ["a1"], "applicability_cites": ["a1"],
    }]}, bundle)
    print(json.dumps({"status": status, "builder_scheduled": admission,
                      "retained_changes": len(checked.changes), "dropped": checked.dropped}))
