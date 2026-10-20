"""Step 5.1d phase 2: calibrate the mood dynamics on DEVELOPMENT data only, then freeze.

    uv run python scripts/mood_calibrate.py --labels var/appraisal_v31/r1            # grid, best configs
    uv run python scripts/mood_calibrate.py --labels var/appraisal_v31/r1 --write    # freeze the best
    uv run python scripts/mood_calibrate.py --labels var/appraisal_v31/r1 --show flat # one config, per target

No model calls: the stored labels (development scenarios, D, 1-vs-10, and the 540-thought
regression replay) are replayed through each candidate. Targets are fixed below, written before
any result was seen. A config must pass under BOTH assumptions for shifts of unclear size (as small
and as clear), so uncertainty about size can't hide a failure. Also reported: the flat model
(inner_state.py, for comparison) and how sensitive the chosen config is to incident errors (all of
a feeling merged into one incident, or every shift its own incident).

The frozen config goes to docs/characters/columbina.inner_state.json. The fresh evaluation set
(scripts/inner_state_fresh_scenarios.json + simulated conversations) is run only after that.
"""
from __future__ import annotations

import argparse
import itertools
import json
import sys
from collections import defaultdict
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
ROOT = BACKEND.parent

from app.ai.mind import inner_state as flat  # noqa: E402
from app.ai.mind import mood_layers as ml  # noqa: E402

BASE = dict(mood=0.3, energy=0.55, interest=0.6, tension=0.15, closeness=0.1)
HALF = dict(mood=30, energy=60, interest=60, tension=10, closeness=1440)
FROZEN = ROOT / "docs" / "characters" / "columbina.inner_state.json"
UNCLEAR = {"as_small": 0.25, "as_clear": 0.5}


def _read(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()] if path.exists() else []


# --- replaying labels ------------------------------------------------------------------------

def _effects(turn: dict, incident_mode: str) -> list[dict]:
    out = []
    for i, e in enumerate(turn["appraisal"]["effects"]):
        inc = e["incident"]
        if incident_mode == "merge":
            inc = f"all-{e['feeling']}"
        elif incident_mode == "split" and e["status"] in ("opened", "escalated"):
            inc = f"m{turn['number']}-{i}"
        out.append(dict(e, incident=inc))
    return out


def trajectory(turns: list[dict], model: str, cfg, unclear: float, incident_mode: str = "as_labelled") -> list[dict]:
    """Values after each turn (at that turn's time)."""
    out = []
    if model == "flat":
        state = flat.start(flat.Dynamics(BASE, HALF), 0.0)
        dyn = flat.Dynamics(BASE, HALF)
    else:
        state = ml.start(0.0)
    for t in turns:
        if "appraisal" not in t:
            continue
        for e in _effects(t, incident_mode):
            if model == "flat":
                amount = 0.0 if e["shift"] == "none" else (unclear if e["size"] == "unclear" else flat.SIZES[e["size"]])
                c = flat.Change("chat", "c", f"m{t['number']}", e["incident"], e["feeling"],
                                -1 if e["status"] == "eased" else 1, amount, e["status"], t["at"])
                state = flat.apply(state, c, dyn).state
            else:
                s = ml.Shift("chat", "c", f"m{t['number']}", e["incident"], e["feeling"], e["status"], e["size"], t["at"])
                state = ml.apply(state, s, cfg).state
        if model == "flat":
            state = flat.fade(state, dyn, t["at"])
            out.append(dict(state.values))
        else:
            state = ml.fade(state, cfg, t["at"])
            out.append(ml.values(state, cfg))
    return out


def mean_traj(runs: list[list[dict]]) -> list[dict]:
    n = min(len(r) for r in runs)
    return [{k: sum(r[i][k] for r in runs) / len(runs) for k in BASE} for i in range(n)]


# --- targets (fixed before any result) -------------------------------------------------------

def targets(data: dict, model: str, cfg, unclear: float, incident_mode: str) -> dict[str, tuple[bool, str]]:
    def T(name):  # mean trajectory of one conversation
        return mean_traj([trajectory(r["turns"], model, cfg, unclear, incident_mode) for r in data[name]])
    out = {}
    routine, trivia = T("routine"), T("new_incident_no_shift")
    out["T1 ordinary stays modest"] = (
        all(max(x["mood"] for x in tr) <= 0.70 for tr in (routine, trivia)),
        f"max mood routine {max(x['mood'] for x in routine):.2f}, trivia {max(x['mood'] for x in trivia):.2f} (<= 0.70)")
    news = T("two_good_news")
    rise6 = news[5]["mood"] - news[4]["mood"]
    out["T2 a second good news still lifts"] = (
        rise6 >= 0.04 and news[-1]["mood"] >= routine[-1]["mood"] + 0.10 and max(x["mood"] for x in news) <= 0.95,
        f"lift at msg 6 {rise6:+.2f} (>= .04), end {news[-1]['mood']:.2f} vs routine {routine[-1]['mood']:.2f} (+.10), max <= .95")
    warm = T("sustained_warmth")
    out["T3 warmth held, disclosures register"] = (
        max(x["mood"] for x in warm) <= 0.92 and warm[6]["mood"] > warm[3]["mood"],
        f"max {max(x['mood'] for x in warm):.2f} (<= .92), msg 7 {warm[6]['mood']:.2f} > msg 4 {warm[3]['mood']:.2f}")
    rep = T("hurt_then_repair")
    excess = rep[4]["tension"] - BASE["tension"]
    drop = rep[4]["tension"] - rep[5]["tension"]
    sr, snr = T("slights_repair"), T("slights_no_repair")
    out["T4 repair eases"] = (
        excess > 0.02 and drop >= 0.4 * excess and sr[-1]["tension"] <= snr[-1]["tension"] - 0.08,
        f"apology drop {drop:.2f} of excess {excess:.2f} (>= 40%); end tension repair {sr[-1]['tension']:.2f} vs none {snr[-1]['tension']:.2f} (-.08)")
    dis, same = T("repeated_dismissal"), T("same_incident_escalates")
    out["T5 dismissal builds"] = (
        dis[-1]["tension"] >= 0.35 and dis[-1]["mood"] <= dis[0]["mood"] - 0.10 and same[-1]["tension"] >= 0.30,
        f"end tension {dis[-1]['tension']:.2f} (>= .35), mood {dis[0]['mood']:.2f} -> {dis[-1]['mood']:.2f}, same-incident {same[-1]['tension']:.2f} (>= .30)")
    one, ten = T("one"), T("ten")
    out["T6 neutral repetition adds nothing"] = (
        abs(one[-1]["tension"] - ten[-1]["tension"]) <= 0.05 and ten[-1]["mood"] - one[-1]["mood"] <= 0.12,
        f"tension one {one[-1]['tension']:.2f} / ten {ten[-1]['tension']:.2f}; mood one {one[-1]['mood']:.2f} / ten {ten[-1]['mood']:.2f}")
    rec = T("recalled_not_active")
    out["T8 a recalled event doesn't lift again"] = (
        rec[7]["mood"] - rec[6]["mood"] <= 0.05, f"change at msg 8 {rec[7]['mood'] - rec[6]['mood']:+.2f} (<= .05)")
    n = pinned = 0
    for ep, rows in data["regression"].items():
        for v in trajectory(rows, model, cfg, unclear, incident_mode):
            n += 1
            pinned += v["mood"] > 0.95 or v["interest"] > 0.95
    out["T7 regression rarely at the limit"] = (pinned / n <= 0.05, f"mood or interest > .95: {pinned / n:.0%} (<= 5%)")
    return out


def load(labels_dir: Path) -> dict:
    data = defaultdict(list)
    for name in ("scenarios", "d", "ten"):
        for r in _read(labels_dir / f"{name}.jsonl"):
            if all("appraisal" in t for t in r["turns"]):
                data[r["name"]].append(r)
    reg = defaultdict(list)
    for r in _read(labels_dir / "relabel.jsonl"):
        if "appraisal" in r:
            reg[r["episode"]].append(r)
    data["regression"] = {k: sorted(v, key=lambda r: r["index"]) for k, v in reg.items()}
    return data


def configs():
    for rh, ks, cap, sq, neg in itertools.product((2.0, 4.0, 8.0), (0.1, 0.2, 0.3, 0.45, 0.6), (None, 0.1, 0.15, 0.3),
                                                  (True, False), (1.0, 2.0, 3.0)):
        keep = {"small": ks, "clear": min(0.9, 2 * ks), "big": min(0.95, 3 * ks), "unclear": ks}
        yield (f"layers rh={rh:g} keep={ks:g} cap={cap} squash={sq} neg={neg:g}",
               ml.Config(BASE, HALF, reaction_half_life_minutes=rh, keep=keep, incident_cap=cap, squash=sq,
                         negative_keep_scale=neg))


def score(data, model, cfg, incident_mode="as_labelled"):
    """Worst case over the unclear-size assumptions: a target passes only if it passes under both."""
    results = {u: targets(data, model, cfg if model == "flat" else ml.Config(**{**cfg.__dict__, "unclear_amount": ua}),
                          ua, incident_mode)
               for u, ua in UNCLEAR.items()}
    names = list(results["as_small"])
    passed = {n: all(results[u][n][0] for u in UNCLEAR) for n in names}
    return passed, results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--labels", type=Path, required=True, help="folder with scenarios/d/ten/relabel jsonl")
    parser.add_argument("--top", type=int, default=8)
    parser.add_argument("--write", action="store_true", help="freeze the best config")
    parser.add_argument("--show", default=None, help="'flat' or 'best': per-target detail")
    args = parser.parse_args()
    data = load(BACKEND / args.labels if not args.labels.is_absolute() else args.labels)
    missing = [n for n in ("routine", "two_good_news", "sustained_warmth", "hurt_then_repair", "repeated_dismissal",
                           "same_incident_escalates", "new_incident_no_shift", "recalled_not_active", "slights_repair",
                           "slights_no_repair", "one", "ten") if not data.get(n)]
    if missing:
        raise SystemExit(f"labels missing for: {missing}")

    flat_pass, flat_res = score(data, "flat", None)
    print(f"flat model (inner_state.py): {sum(flat_pass.values())}/{len(flat_pass)} targets")
    ranked = []
    for name, cfg in configs():
        passed, res = score(data, "layers", cfg)
        # Incident matching is ~58% right, so prefer configs that still pass with incidents merged or split.
        robust = min(sum(score(data, "layers", cfg, mode)[0].values()) for mode in ("merge", "split"))
        overshoot = max(max(x["mood"] for x in mean_traj([trajectory(r["turns"], "layers",
                        ml.Config(**{**cfg.__dict__, "unclear_amount": 0.5}), 0.5) for r in data[c]]))
                        for c in ("two_good_news", "sustained_warmth"))
        ranked.append((sum(passed.values()), robust, -overshoot, name, cfg, passed, res))
    ranked.sort(key=lambda r: (r[0], r[1], r[2]), reverse=True)
    print(f"\n{len(ranked)} layered configs; best:")
    for n, robust, _, name, _, passed, _ in ranked[:args.top]:
        print(f"  {n}/{len(passed)} (incidents merged/split: worst {robust})  {name}  "
              f"(failed: {[k for k, v in passed.items() if not v]})")

    best = ranked[0]
    if args.show:
        res = flat_res if args.show == "flat" else best[6]
        print(f"\n{args.show}: per target (as_small | as_clear)")
        for t in res["as_small"]:
            print(f"  {'PASS' if all(res[u][t][0] for u in UNCLEAR) else 'fail'} {t}\n     small: {res['as_small'][t][1]}\n     clear: {res['as_clear'][t][1]}")
    print(f"\nincident sensitivity of the best ({best[3]}):")
    for mode in ("as_labelled", "merge", "split"):
        passed, _ = score(data, "layers", best[4], mode)
        print(f"  {mode:12s} {sum(passed.values())}/{len(passed)}  failed: {[k for k, v in passed.items() if not v]}")
    if args.write:
        cfg = best[4]
        FROZEN.write_text(json.dumps(dict(
            _comment="Inner-state dynamics (app/ai/mind/mood_layers.py), frozen by scripts/mood_calibrate.py on "
                     "development data only. Do not tune on the fresh evaluation set; changing this file makes "
                     "that set development data.",
            labels=str(args.labels), chosen=best[3], targets_passed=f"{best[0]}/{len(best[5])}",
            robust_to_incident_errors=f"{best[1]}/{len(best[5])}",
            baseline=cfg.baseline, half_life_minutes=cfg.half_life_minutes,
            reaction_half_life_minutes=cfg.reaction_half_life_minutes, keep=cfg.keep,
            incident_cap=cfg.incident_cap, squash=cfg.squash, negative_keep_scale=cfg.negative_keep_scale,
            unclear_amount=dict(note="no single value: results are reported as small and as clear",
                                range=list(UNCLEAR.values()))), indent=1))
        print(f"\nfrozen: {FROZEN}")


if __name__ == "__main__":
    main()
