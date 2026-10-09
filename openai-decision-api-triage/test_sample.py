"""Test the asset-intake sample against the live Decisions API.

Sends the sample and control cases to POST /v1/decisions, validates every answer against the
documented response shape, checks what the model decided, runs the sample end to
end through triage(), and saves everything to results/ (git-ignored).

  export OPENAI_API_KEY=sk-...
  python test_sample.py
  python test_sample.py --image ~/photos/hiker.jpg --alt "Hiker on a ridge at dusk" --runs 3

Uses only the standard library, so it does not need an SDK with Decisions support.
Exit code is 1 if a response breaks the documented shape. When the model simply
judges differently from what we expected, the check is a WARN, because that
judgement is what you are measuring.
"""
import argparse
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

# OPENAI_BASE_URL works as it does for the OpenAI SDK (proxies, gateways, test servers).
API_URL = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/") + "/decisions"
SAMPLE_IMAGE = FIX / "sample-asset.png"
# Correct alt text for the sample image, which is an illustration, not a photo.
SAMPLE_ALT = ("An illustration of a smiling hiker in a blue waterproof jacket on a mountain "
              "trail at sunset.")

results = []


def check(ok, label, detail="", hard=True):
    status = "PASS" if ok else ("FAIL" if hard else "WARN")
    results.append({"status": status, "check": label, "detail": detail})
    print(f"  [{status}] {label}" + (f": {detail}" if detail else ""))
    return ok


def validate_answers(req, answers):
    """Check answers against the documented response shape."""
    by_q = {q["name"]: q for q in req["questions"]}
    check(set(answers) == set(by_q), "one answer per question name", f"got {sorted(answers)}")
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


def live(key, image, runs, alt):
    meta = (FIX / "metadata.txt").read_text()
    is_sample = image.resolve() == SAMPLE_IMAGE.resolve()
    # The metadata's alt text describes a studio packshot, so it should not match.
    expect_wrong = {"alt_text_matches": ("<", 0.5)}
    if is_sample:
        expect_wrong["asset_type"] = ("==", "graphic")   # the sample is an illustration
    cases = [("sample: wrong alt text", t.intake_request(image, meta), expect_wrong)]
    alt = alt or (SAMPLE_ALT if is_sample else None)
    if alt:
        correct_meta = "\n".join(f"Alt text: {alt}" if l.startswith("Alt text:") else l
                                  for l in meta.splitlines())
        cases.append(("control: correct alt text", t.intake_request(image, correct_meta),
                      {"alt_text_matches": (">=", t.ALT_TEXT_MIN)}))
    else:
        print("No control case: pass --alt with the correct alt text for your image to add one.")
    cases += [
        ("release note: signed release", t.release_request(meta, (FIX / "rights.txt").read_text()),
         {"release_confirmed": (">=", t.RELEASE_MIN)}),
        ("release note: no release", t.release_request(meta, (FIX / "rights-missing.txt").read_text()),
         {"release_confirmed": ("<", t.RELEASE_MIN)}),
    ]
    record = {"run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "image": str(image), "image_bytes": image.stat().st_size, "cases": []}
    for label, req, expect in cases:
        print(f"\n{label}")
        latencies, answers, raw = [], None, None
        for _ in range(runs):
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
    return out, answers_out


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--image", type=Path, default=SAMPLE_IMAGE, help="image to test with")
    p.add_argument("--alt", help="correct alt text for --image, to add a control case")
    p.add_argument("--runs", type=int, default=1, help="calls per case, for latency and stability")
    args = p.parse_args()

    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        sys.exit("OPENAI_API_KEY is not set")
    image = args.image.expanduser()
    if image.suffix.lower() not in t.MIME:
        sys.exit(f"unsupported image type {image.suffix}; use one of {', '.join(t.MIME)}")

    out, answers_out = live(key, image, args.runs, args.alt)

    counts = {s: sum(r["status"] == s for r in results) for s in ("PASS", "WARN", "FAIL")}
    print(f"\n{counts['PASS']} passed, {counts['WARN']} warnings, {counts['FAIL']} failed")
    print(f"Saved {out.relative_to(HERE)} and {answers_out.relative_to(HERE)}")
    print("\nNext steps:")
    print("  - Read the WARN lines: they are where the model judged differently from what we expected.")
    print("  - Try your own assets: --image <photo>, and edit fixtures/metadata.txt and rights.txt.")
    print("  - Tune the thresholds at the top of asset_triage.py once you have labelled examples.")
    return 1 if counts["FAIL"] else 0


if __name__ == "__main__":
    sys.exit(main())
