"""Test the asset-intake sample, offline or against the live Decisions API.

Offline (no key needed): checks the request shape and the routing policy.
  python test_sample.py

Live: sends the sample and control cases to POST /v1/decisions, validates every
answer against the documented shape, checks expectations, and saves the results
to results/ (git-ignored).
  OPENAI_API_KEY=sk-... python test_sample.py --live
  OPENAI_API_KEY=sk-... python test_sample.py --live --image ~/photos/hiker.jpg

Uses only the standard library, so it does not need an SDK with Decisions support.
Exit code is 1 if any hard check fails. Expectation misses are reported as WARN,
because the model's judgement on your image is what you are measuring.
"""
import argparse
import copy
import json
import math
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).parent
FIX = HERE / "fixtures"
RESULTS = HERE / "results"
sys.path.insert(0, str(HERE))
import asset_triage as t  # noqa: E402

API_URL = "https://api.openai.com/v1/decisions"
CORRECT_ALT = ("Alt text: A smiling hiker in a blue waterproof jacket on a mountain "
               "trail at sunset.")

results = []


def check(ok, label, detail="", hard=True):
    status = "PASS" if ok else ("FAIL" if hard else "WARN")
    results.append({"status": status, "check": label, "detail": detail})
    print(f"  [{status}] {label}" + (f": {detail}" if detail else ""))
    return ok


# ---------- shape validation (shared by offline and live) ----------

def validate_request(req):
    names = [q["name"] for q in req["questions"]]
    check(len(names) == len(set(names)), "question names are unique", ", ".join(names))
    check(req["model"] == "gpt-6-luna", "model is gpt-6-luna")
    for q in req["questions"]:
        check(q["type"] in ("predicate", "choice", "score"), f"{q['name']}: known type", q["type"])
        check(bool(q.get("instructions")), f"{q['name']}: has instructions")
        if q["type"] == "choice":
            vals = [c["value"] for c in q["choices"]]
            check(len(vals) == len(set(vals)) and len(vals) >= 2, f"{q['name']}: distinct choices", ", ".join(vals))
        if q["type"] == "score":
            check(len(q["levels"]) >= 2, f"{q['name']}: at least two levels", str(len(q["levels"])))
    if isinstance(req["input"], list):
        parts = req["input"][0]["content"]
        img = [p for p in parts if p["type"] == "input_image"]
        for p in img:
            check(p["image_url"].startswith("data:image/") and ";base64," in p["image_url"],
                  "image is an inline base64 data URL", f"{len(p['image_url']):,} chars")


def validate_answers(req, answers):
    """Check answers against the documented response shape."""
    by_q = {q["name"]: q for q in req["questions"]}
    check(set(answers) == set(by_q), "one answer per question name",
          f"got {sorted(answers)}")
    for name, a in answers.items():
        q = by_q.get(name)
        if q is None:
            continue
        if a["type"] == "refusal":
            check(False, f"{name}: answered", "refusal", hard=False)
            continue
        check(a["type"] == q["type"], f"{name}: answer type matches", a["type"])
        if a["type"] == "predicate":
            p = a.get("probability")
            check(isinstance(p, (int, float)) and 0 <= p <= 1, f"{name}: probability in [0,1]", f"{p}")
        elif a["type"] == "choice":
            allowed = [c["value"] for c in q["choices"]]
            check(a.get("choice") in allowed, f"{name}: choice is a supplied value", f"{a.get('choice')}")
            probs = a.get("probabilities") or []
            total = sum(x["probability"] for x in probs)
            check(math.isclose(total, 1, abs_tol=0.02), f"{name}: probabilities sum to 1", f"{total:.3f}")
            check(0 <= a.get("confidence", -1) <= 1, f"{name}: confidence in [0,1]", f"{a.get('confidence')}")
        elif a["type"] == "score":
            top = len(q["levels"]) - 1
            s = a.get("score")
            check(isinstance(s, (int, float)) and 0 <= s <= top, f"{name}: score in [0,{top}]", f"{s}")
            probs = a.get("probabilities") or []
            if probs:
                weighted = sum(x["value"] * x["probability"] for x in probs)
                check(math.isclose(weighted, s, abs_tol=0.05), f"{name}: score = weighted average",
                      f"{s} vs {weighted:.3f}", hard=False)


# ---------- offline ----------

def offline():
    print("\nRequest shape (fixtures)")
    meta = (FIX / "metadata.txt").read_text()
    req1 = t.intake_request(FIX / "sample-asset.png", meta)
    validate_request(req1)
    validate_request(t.release_request((FIX / "rights.txt").read_text()))

    print("\nIllustrative answers match the documented shape")
    sample = json.loads((FIX / "sample-answers.json").read_text())
    intake_only = {k: v for k, v in sample.items() if k != "release_confirmed"}
    validate_answers(req1, intake_only)

    print("\nRouting policy")
    cases = [
        ("sample as captured", lambda a: None, ("review", ["alt text does not match the image"])),
        ("alt text fixed", lambda a: a["alt_text_matches"].update(probability=0.93), ("auto_approve", [])),
        ("people, no release check", lambda a: (a["alt_text_matches"].update(probability=0.93),
                                                a.pop("release_confirmed")),
         ("review", ["people visible, release not checked"])),
        ("release not confirmed", lambda a: (a["alt_text_matches"].update(probability=0.93),
                                             a["release_confirmed"].update(probability=0.2)),
         ("review", ["people visible, release not confirmed"])),
        ("watermark", lambda a: (a["alt_text_matches"].update(probability=0.93),
                                 a["third_party_mark"].update(probability=0.6)),
         ("review", ["possible third-party logo or watermark"])),
        ("needs edits", lambda a: (a["alt_text_matches"].update(probability=0.93),
                                   a["publish_readiness"].update(score=1.1)),
         ("review", ["needs edits (quality 1.10 of 2)"])),
        ("unusable image", lambda a: a["publish_readiness"].update(score=0.4),
         ("return_to_uploader", ["image quality score 0.40 of 2"])),
        ("refusal", lambda a: a.update(third_party_mark={"type": "refusal", "name": "third_party_mark"}),
         ("review", ["model refused: third_party_mark"])),
    ]
    for label, mutate, expected in cases:
        a = copy.deepcopy(sample)
        mutate(a)
        got = t.route(a)
        check(got == expected, label, f"{got[0]} {got[1]}")

    print("\ntriage(): request 2 runs only when people are found")
    for people, want_calls in ((0.94, 2), (0.10, 1)):
        calls = []

        def fake_ask(req, people=people):
            calls.append([q["name"] for q in req["questions"]])
            answers = {q["name"]: copy.deepcopy(sample[q["name"]]) for q in req["questions"]}
            if "identifiable_people" in answers:
                answers["identifiable_people"]["probability"] = people
            return answers

        (route, reasons), _ = t.triage(fake_ask, FIX / "sample-asset.png", meta,
                                       (FIX / "rights.txt").read_text())
        check(len(calls) == want_calls, f"people {people}: {want_calls} request(s)",
              f"{route} {reasons}")


# ---------- live ----------

def call(req, key):
    body = json.dumps(req).encode()
    r = urllib.request.Request(API_URL, data=body, method="POST", headers={
        "Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    start = time.perf_counter()
    try:
        with urllib.request.urlopen(r, timeout=60) as resp:
            raw = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:600]
        raise SystemExit(f"\nHTTP {e.code} from {API_URL}:\n{detail}")
    ms = (time.perf_counter() - start) * 1000
    answers = {a["name"]: a for a in raw.get("answers", [])}
    return answers, raw, ms


def live(key, image, runs):
    meta = (FIX / "metadata.txt").read_text()
    correct_meta = "\n".join(CORRECT_ALT if l.startswith("Alt text:") else l for l in meta.splitlines())
    cases = [
        ("sample: wrong alt text", t.intake_request(image, meta),
         {"alt_text_matches": ("<", 0.5), "asset_type": ("==", "lifestyle")}),
        ("control: correct alt text", t.intake_request(image, correct_meta),
         {"alt_text_matches": (">=", t.ALT_TEXT_MIN)}),
        ("release note: signed release", t.release_request((FIX / "rights.txt").read_text()),
         {"release_confirmed": (">=", t.RELEASE_MIN)}),
        ("release note: no release", t.release_request((FIX / "rights-missing.txt").read_text()),
         {"release_confirmed": ("<", t.RELEASE_MIN)}),
    ]
    record = {"run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "image": str(image), "image_bytes": image.stat().st_size, "cases": []}
    for label, req, expect in cases:
        print(f"\n{label}")
        latencies, answers, raw = [], None, None
        for i in range(runs):
            answers, raw, ms = call(req, key)
            latencies.append(round(ms))
        print(f"  latency ms: {latencies}")
        validate_answers(req, answers)
        for name, (op, want) in expect.items():
            a = answers.get(name, {})
            got = a.get("probability", a.get("choice"))
            ok = {"<": lambda: got is not None and got < want, ">=": lambda: got is not None and got >= want,
                  "==": lambda: got == want}[op]()
            check(ok, f"expect {name} {op} {want}", f"got {got}", hard=False)
        record["cases"].append({"case": label, "latency_ms": latencies, "answers": answers,
                                "usage": raw.get("usage"), "raw": raw})

    # End-to-end: the sample's real route, with the release check only if needed.
    print("\nEnd-to-end: triage() on the sample, as asset_triage.py runs it")
    (route, reasons), sample_answers = t.triage(
        lambda req: call(req, key)[0], image, (FIX / "metadata.txt").read_text(),
        (FIX / "rights.txt").read_text())
    check("release_confirmed" in sample_answers, "release check triggered (people found)",
          f"identifiable_people = {sample_answers.get('identifiable_people', {}).get('probability')}"
          + ("" if "release_confirmed" in sample_answers
             else "; an illustration may not count as a real face, try --image with a photo"), hard=False)
    print(f"  route: {route} {reasons}")
    record["sample_route"] = {"route": route, "reasons": reasons, "answers": sample_answers}

    RESULTS.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = RESULTS / f"live-run-{stamp}.json"
    out.write_text(json.dumps(record, indent=2))
    answers_out = RESULTS / "live-answers.json"
    answers_out.write_text(json.dumps(sample_answers, indent=2))
    print(f"\nSaved {out.relative_to(HERE)} and {answers_out.relative_to(HERE)}")
    print(f"Replay the policy on them: python asset_triage.py --replay {answers_out.relative_to(HERE)}")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--live", action="store_true", help="also call the Decisions API (needs OPENAI_API_KEY)")
    p.add_argument("--image", type=Path, default=FIX / "sample-asset.png", help="image to test with")
    p.add_argument("--runs", type=int, default=1, help="calls per live case, for latency and stability")
    args = p.parse_args()

    offline()
    if args.live:
        key = os.environ.get("OPENAI_API_KEY")
        if not key:
            sys.exit("OPENAI_API_KEY is not set")
        if args.image.suffix.lower() not in t.MIME:
            sys.exit(f"unsupported image type {args.image.suffix}; use one of {', '.join(t.MIME)}")
        live(key, args.image.expanduser(), args.runs)

    counts = {s: sum(r["status"] == s for r in results) for s in ("PASS", "WARN", "FAIL")}
    print(f"\n{counts['PASS']} passed, {counts['WARN']} warnings, {counts['FAIL']} failed")
    return 1 if counts["FAIL"] else 0


if __name__ == "__main__":
    sys.exit(main())
