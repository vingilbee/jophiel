"""Spot-check page: a local HTML file of random turns to thumbs-up / thumbs-down.

Votes are kept in the browser while reviewing and exported as a JSON file; ``import_votes`` copies
it into the run, where training drops thumbs-down samples and weights thumbs-up ones higher.
"""
from __future__ import annotations

import html
import json
import random
from pathlib import Path

from ..contracts import ACTS, DIALS, FEELINGS, GROUP_LABELS, NO_SECOND, SCALE
from .data import load_episodes

VOTES_FILE = 'review_votes.json'


def _labels(sample: dict) -> str:
    u, d = sample['understand'], sample['decide']
    acts = ', '.join(a.replace('_', ' ') for a in ACTS if u['acts'][a]) or '—'
    felt = ', '.join(f'{f} {u["feelings"][f].replace("_", " ")}' for f in FEELINGS if SCALE[u['feelings'][f]] > 0) or 'nothing much'
    moves = GROUP_LABELS[d['main']] + ('' if d['second'] == NO_SECOND else f' → {GROUP_LABELS[d["second"]]}')
    dials = ' · '.join(f'{k} {d["dials"][k].replace("_", " ")}' for k in DIALS)
    return (f'<dl><dt>Read as</dt><dd>{html.escape(acts)} · tone {u["tone"]} · {u["request"].replace("_", " ")}</dd>'
            f'<dt>Feels</dt><dd>{html.escape(felt)}</dd><dt>Does</dt><dd><b>{html.escape(moves)}</b></dd>'
            f'<dt>How</dt><dd>{html.escape(dials)}</dd></dl>')


def build(run_dir: Path, count: int = 40, seed: int = 0) -> Path:
    episodes = load_episodes(run_dir)
    turns = [(ep, t) for ep in episodes for t in ep['turns'] if t.get('continued') is not None and not ep.get('real')]
    random.Random(seed).shuffle(turns)
    cards = []
    for ep, t in turns[:count]:
        sample = t['samples'][t['continued']][-1]
        key = f'{ep["scenario"]["id"]}:{t["index"]}:{sample["index"]}'
        window = ''.join(f'<p class="{"them" if m["speaker"] == "user" else "her"}">{html.escape(m["text"])}</p>'
                         for m in t['bundle']['window'][-4:])
        mood = ', '.join(f'{k} {v:+.2f}' for k, v in t['bundle']['mood'].items())
        cards.append(f'''<article data-key="{key}">
<header><span>{html.escape(ep["scenario"]["user_type"])} · {html.escape(ep["scenario"]["situation"])}</span><span class="muted">mood {mood}</span></header>
<div class="chat">{window}<p class="them now">{html.escape(t["bundle"]["message"])}</p><p class="her now">{html.escape(t["reply"])}</p></div>
<details><summary>Her thought and labels</summary><p class="thought">{html.escape(sample["thought"])}</p>{_labels(sample)}</details>
<footer><button data-vote="up" aria-label="Thumbs up">👍 her</button><button data-vote="down" aria-label="Thumbs down">👎 not her</button>
<input placeholder="why (optional)" aria-label="Note"></footer></article>''')
    page = f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Teacher Spot Check</title><style>
:root{{--bg:#f6f6f4;--card:#fff;--text:#1d1d1b;--muted:#6b6b66;--line:#e2e2dc;--them:#ececE6;--her:#dff1ee;--accent:#0f766e;--down:#b42318}}
@media (prefers-color-scheme:dark){{:root:not([data-theme="light"]){{--bg:#121212;--card:#1c1c1c;--text:#ececec;--muted:#9a9a94;--line:#2c2c2c;--them:#262626;--her:#163532;--accent:#5eead4;--down:#f97066}}}}
:root[data-theme="dark"]{{--bg:#121212;--card:#1c1c1c;--text:#ececec;--muted:#9a9a94;--line:#2c2c2c;--them:#262626;--her:#163532;--accent:#5eead4;--down:#f97066}}
body{{margin:0;background:var(--bg);color:var(--text);font:15px/1.5 system-ui,sans-serif}}
main{{max-width:720px;margin:0 auto;padding:16px}}h1{{font-size:20px;margin:8px 0}}.muted{{color:var(--muted)}}
article{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px;margin:14px 0}}
header{{display:flex;justify-content:space-between;gap:8px;flex-wrap:wrap;font-size:13px;margin-bottom:8px}}
.chat p{{margin:4px 0;padding:6px 10px;border-radius:10px;max-width:85%;white-space:pre-wrap;overflow-wrap:anywhere}}
.them{{background:var(--them)}}.her{{background:var(--her);margin-left:auto!important}}.now{{font-weight:600}}
details{{margin-top:8px;font-size:14px}}.thought{{font-style:italic;color:var(--muted)}}dl{{display:grid;grid-template-columns:auto 1fr;gap:2px 10px;margin:6px 0}}dt{{color:var(--muted)}}dd{{margin:0}}
footer{{display:flex;gap:8px;margin-top:10px;flex-wrap:wrap}}button{{border:1px solid var(--line);background:var(--card);color:var(--text);border-radius:8px;padding:6px 12px;cursor:pointer;font:inherit}}
button.on[data-vote=up]{{border-color:var(--accent);color:var(--accent)}}button.on[data-vote=down]{{border-color:var(--down);color:var(--down)}}
input{{flex:1;min-width:140px;border:1px solid var(--line);background:var(--bg);color:var(--text);border-radius:8px;padding:6px 10px;font:inherit}}
.bar{{position:sticky;top:0;background:var(--bg);padding:8px 0;display:flex;gap:10px;align-items:center;justify-content:space-between;border-bottom:1px solid var(--line)}}
</style></head><body><main>
<h1>Is this her?</h1><p class="muted">{len(cards)} random turns from <code>{html.escape(run_dir.name)}</code>. Judge by her character page, not by how nice she is. Votes stay in this browser until you export them.</p>
<div class="bar"><span id="count" class="muted"></span><button id="export">Export votes</button></div>
{''.join(cards)}
</main><script>
const STORE='teacher-review-{html.escape(run_dir.name)}';let votes={{}};
try{{votes=JSON.parse(localStorage.getItem(STORE)||'{{}}')}}catch(e){{}}
const save=()=>{{try{{localStorage.setItem(STORE,JSON.stringify(votes))}}catch(e){{}};document.getElementById('count').textContent=Object.keys(votes).length+' of {len(cards)} reviewed'}};
document.querySelectorAll('article').forEach(a=>{{const k=a.dataset.key,input=a.querySelector('input');
 const paint=()=>a.querySelectorAll('button').forEach(b=>b.classList.toggle('on',votes[k]?.vote===b.dataset.vote));
 if(votes[k]){{input.value=votes[k].note||''}};paint();
 a.querySelectorAll('button').forEach(b=>b.onclick=()=>{{votes[k]={{vote:b.dataset.vote,note:input.value}};paint();save()}});
 input.oninput=()=>{{if(votes[k]){{votes[k].note=input.value;save()}}}};}});
document.getElementById('export').onclick=()=>{{const blob=new Blob([JSON.stringify(votes,null,1)],{{type:'application/json'}});
 const u=URL.createObjectURL(blob),l=document.createElement('a');l.href=u;l.download='{VOTES_FILE}';l.click();URL.revokeObjectURL(u)}};
save();
</script></body></html>'''
    path = run_dir / 'review.html'
    path.write_text(page)
    return path


def import_votes(run_dir: Path, votes_path: Path) -> int:
    votes = json.loads(votes_path.read_text())
    (run_dir / VOTES_FILE).write_text(json.dumps(votes, indent=1))
    return len(votes)


def load_votes(run_dir: Path) -> dict[str, dict]:
    path = run_dir / VOTES_FILE
    return json.loads(path.read_text()) if path.exists() else {}
