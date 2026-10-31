"""Student 2, the decision model her cognition runs live: make a servable copy of a trained adapter, and serve it.

  uv run --with peft --with mlx-lm==0.32.2 python scripts/student_model.py prepare --run qwen-4 --adapter var/cognitive_simple/qwen-4/qwen_adapter-2/adapter
  uv run --with mlx-lm==0.32.2 python scripts/student_model.py serve --model var/cognitive_simple/qwen-4/serve/mlx-8bit

``prepare`` merges the LoRA adapter into the base it was trained on (Qwen3 cast to fp16, every decoder Linear rounded
to nf4 and back, as the 4-bit training saw it; merged in fp32, saved in fp16), converts that to MLX at ``--bits``
(8 by default), and writes ``student.json`` next to the weights: everything the backend needs to talk to this model
(the bundle's system prompt, output order and situation rendering, the snapshot/decision versions). Swapping models
is a new ``prepare`` and pointing ``COGNITION_STUDENT_DIR`` at its folder (and ``serve`` at the same folder).

``serve`` runs mlx-lm's HTTP server on ``COGNITION_STUDENT_URL``'s port, loopback only. The backend never imports
MLX: it reads ``student.json`` and sends rendered situations to that server (``services/student_two.py``).
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import zipfile
from pathlib import Path
from urllib.parse import urlparse

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
RUNS = BACKEND / 'var' / 'cognitive_simple'


def _merge(adapter: Path, base_name: str, out: Path) -> None:
    import bitsandbytes.functional as F
    import torch
    from peft import PeftModel
    from transformers import AutoModelForCausalLM, AutoTokenizer

    tok = AutoTokenizer.from_pretrained(base_name)
    base = AutoModelForCausalLM.from_pretrained(base_name, dtype=torch.float16)
    with torch.no_grad():
        for _, mod in base.model.layers.named_modules():
            if isinstance(mod, torch.nn.Linear):
                q, s = F.quantize_4bit(mod.weight.data, blocksize=64, quant_type='nf4', compress_statistics=True)
                mod.weight.data = F.dequantize_4bit(q, s).to(torch.float16)
    model = PeftModel.from_pretrained(base.to(torch.float32), str(adapter)).merge_and_unload().to(torch.float16)
    model.save_pretrained(out)
    tok.save_pretrained(out)


def _prepare(args) -> None:
    from app.ai.Cognitive.simple.events import DECISION_VERSION, SNAPSHOT_VERSION

    run = RUNS / args.run
    adapter = Path(args.adapter).resolve()
    export = json.loads((run / 'export.json').read_text())
    with zipfile.ZipFile(run / 'bundle.zip') as z:
        system = json.loads(z.read('prompts.json'))['student_system']
    base_name = json.loads((adapter / 'adapter_config.json').read_text())['base_model_name_or_path']
    serve = run / 'serve'
    merged, mlx = serve / 'merged-fp16', serve / f'mlx-{args.bits}bit'
    if not (merged / 'config.json').exists():
        print('merging', adapter, 'into', base_name)
        _merge(adapter, base_name, merged)
    if not (mlx / 'config.json').exists():
        print('converting to MLX', args.bits, 'bit')
        from mlx_lm import convert
        convert(str(merged), mlx_path=str(mlx), quantize=True, q_bits=args.bits, q_group_size=64)
    manifest = dict(
        name=args.name or args.run, base=base_name, adapter=str(adapter.relative_to(BACKEND)), format='mlx',
        bits=args.bits, system=system, order=export.get('order', 'work_first'),
        # How situations were rendered in training: None = before the exchange line (qwen-4), else its version.
        rendering=export.get('rendering'), snapshot_version=SNAPSHOT_VERSION, decision_version=DECISION_VERSION,
        # Trained and scored without Qwen3's thinking block.
        chat_template_kwargs={'enable_thinking': False}, max_tokens=args.max_tokens, trained_on=export.get('runs', []))
    (mlx / 'student.json').write_text(json.dumps(manifest, indent=1))
    print('ready:', mlx.relative_to(BACKEND))


def _serve(args) -> None:
    from app.core.config import get_settings

    url = urlparse(get_settings().cognition_student_url)
    model = Path(args.model).resolve()
    if not (model / 'student.json').exists():
        sys.exit(f'{model} has no student.json: run prepare first')
    manifest = json.loads((model / 'student.json').read_text())
    subprocess.run([sys.executable, '-m', 'mlx_lm.server', '--model', str(model), '--host', '127.0.0.1',
                    '--port', str(url.port or 8792),
                    '--chat-template-args', json.dumps(manifest.get('chat_template_kwargs', {}))], check=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('prepare', help='merge, convert to MLX, write student.json')
    p.add_argument('--run', required=True, help='folder under var/cognitive_simple with bundle.zip and export.json')
    p.add_argument('--adapter', required=True, help='the trained LoRA adapter folder')
    p.add_argument('--name', help='model name in traces (default: the run)')
    p.add_argument('--bits', type=int, default=8, choices=(4, 8))
    p.add_argument('--max-tokens', type=int, default=400)
    p.set_defaults(func=_prepare)
    s = sub.add_parser('serve', help='serve a prepared model over loopback HTTP')
    s.add_argument('--model', required=True, help='a prepared folder (with student.json)')
    s.set_defaults(func=_serve)
    args = parser.parse_args()
    args.func(args)


if __name__ == '__main__':
    main()
