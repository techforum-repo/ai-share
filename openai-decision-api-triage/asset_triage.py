"""Asset intake triage with the OpenAI Decisions API (public beta).

Request 1 sends the image and its metadata together and asks five independent
questions. Request 2 is text only and runs only when request 1 finds
identifiable people. A plain-Python policy turns the answers into a route.

Usage:
  python asset_triage.py photo.jpg metadata.txt rights.txt      # calls the API
  python asset_triage.py photo.jpg metadata.txt rights.txt --dry-run
  python asset_triage.py --replay fixtures/sample-answers.json   # policy only

Needs OPENAI_API_KEY and an OpenAI Python SDK with Decisions support (3.26.0+).
"""
import argparse
import base64
import json
import sys
from pathlib import Path

MODEL = "gpt-6-luna"
MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}

# Request 1: independent questions about one asset (image + metadata text).
INTAKE_QUESTIONS = [
    {
        "type": "predicate",
        "name": "alt_text_matches",
        "instructions": "Does the alt text in the metadata accurately describe the main subject "
                        "and setting of the image? Answer false if it describes something that is "
                        "not visible or leaves out the main subject.",
    },
    {
        "type": "predicate",
        "name": "identifiable_people",
        "instructions": "Is at least one person's face clearly visible and identifiable? Ignore "
                        "people who are very small, blurred, or seen only from behind.",
    },
    {
        "type": "predicate",
        "name": "third_party_mark",
        "instructions": "Does the image show a logo, brand name, or watermark that does not belong "
                        "to the uploader's brand named in the metadata? Include stock-photo watermarks.",
    },
    {
        "type": "choice",
        "name": "asset_type",
        "instructions": "What kind of asset is this?",
        "choices": [
            {"value": "product", "description": "A product on its own, studio or plain background."},
            {"value": "lifestyle", "description": "A product in use, in a real setting, often with people."},
            {"value": "graphic", "description": "Illustration, infographic, banner, or text-heavy design."},
            {"value": "screenshot", "description": "A capture of a screen, app, or document."},
            {"value": "other", "description": "Anything that fits none of the above."},
        ],
    },
    {
        "type": "score",
        "name": "publish_readiness",
        "instructions": "How ready is this image for a public web page, judged only on focus, "
                        "exposure, framing, and visible defects?",
        "levels": [
            {"label": "Unusable", "description": "Blurred, badly exposed, cropped through the subject, or damaged."},
            {"label": "Needs edits", "description": "Usable after a crop, colour, or retouch pass."},
            {"label": "Ready", "description": "Sharp, well exposed, and well framed as delivered."},
        ],
    },
]

# Request 2: depends on request 1, so it is a separate call. Text only.
RELEASE_QUESTION = {
    "type": "predicate",
    "name": "release_confirmed",
    "instructions": "Does this rights note confirm a signed model release that covers every "
                    "identifiable person and the intended usage stated in the note?",
}

# Starting thresholds. Tune them on labelled assets from your own library.
ALT_TEXT_MIN = 0.70
PEOPLE_MIN = 0.50
THIRD_PARTY_MAX = 0.30
RELEASE_MIN = 0.80
READY_MIN = 1.50      # score runs 0 (Unusable) to 2 (Ready)
REJECT_BELOW = 0.75


def image_part(path):
    mime = MIME[path.suffix.lower()]
    data = base64.b64encode(path.read_bytes()).decode("ascii")
    return {"type": "input_image", "image_url": f"data:{mime};base64,{data}"}


def intake_request(image_path, metadata):
    return {
        "model": MODEL,
        "input": [{
            "role": "user",
            "content": [
                {"type": "input_text", "text": "Asset submitted to the DAM.\n" + metadata},
                image_part(image_path),
            ],
        }],
        "questions": INTAKE_QUESTIONS,
    }


def release_request(rights_note):
    return {"model": MODEL, "input": rights_note, "questions": [RELEASE_QUESTION]}


def ask(client, request):
    """Call /v1/decisions and return answers keyed by question name."""
    decision = client.decisions.create(**request)
    return {a.name: a.model_dump() for a in decision.answers}


def triage(ask_fn, image_path, metadata, rights_note):
    """Request 1, request 2 only when people are found, then the policy."""
    answers = ask_fn(intake_request(image_path, metadata))
    if answers.get("identifiable_people", {}).get("probability", 0) >= PEOPLE_MIN:
        answers.update(ask_fn(release_request(rights_note)))
    return route(answers), answers


def route(answers):
    """Turn answers into (route, reasons). Refusals and gaps go to a person."""
    refused = [n for n, a in answers.items() if a["type"] == "refusal"]
    if refused:
        return "review", [f"model refused: {', '.join(refused)}"]

    readiness = answers["publish_readiness"]["score"]
    if readiness < REJECT_BELOW:
        return "return_to_uploader", [f"image quality score {readiness:.2f} of 2"]

    reasons = []
    if answers["alt_text_matches"]["probability"] < ALT_TEXT_MIN:
        reasons.append("alt text does not match the image")
    if answers["third_party_mark"]["probability"] >= THIRD_PARTY_MAX:
        reasons.append("possible third-party logo or watermark")
    if readiness < READY_MIN:
        reasons.append(f"needs edits (quality {readiness:.2f} of 2)")
    if answers["identifiable_people"]["probability"] >= PEOPLE_MIN:
        release = answers.get("release_confirmed")
        if release is None or release["type"] == "refusal":
            reasons.append("people visible, release not checked")
        elif release["probability"] < RELEASE_MIN:
            reasons.append("people visible, release not confirmed")
    return ("review", reasons) if reasons else ("auto_approve", [])


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("image", nargs="?", type=Path)
    p.add_argument("metadata", nargs="?", type=Path)
    p.add_argument("rights", nargs="?", type=Path)
    p.add_argument("--dry-run", action="store_true", help="print request 1 without calling the API")
    p.add_argument("--replay", type=Path, help="run the policy on saved answers")
    args = p.parse_args()

    if args.replay:
        answers = json.loads(args.replay.read_text())
    else:
        if not (args.image and args.metadata and args.rights):
            p.error("image, metadata and rights are required unless --replay is used")
        request = intake_request(args.image, args.metadata.read_text())
        if args.dry_run:
            shown = json.loads(json.dumps(request))
            url = shown["input"][0]["content"][1]["image_url"]
            shown["input"][0]["content"][1]["image_url"] = url[:40] + f"...({len(url)} chars)"
            print(json.dumps(shown, indent=2))
            return
        from openai import OpenAI
        client = OpenAI()
        (decision, reasons), answers = triage(lambda r: ask(client, r), args.image,
                                              args.metadata.read_text(), args.rights.read_text())
        print(json.dumps({"route": decision, "reasons": reasons, "answers": answers}, indent=2))
        return

    decision, reasons = route(answers)
    print(json.dumps({"route": decision, "reasons": reasons, "answers": answers}, indent=2))


if __name__ == "__main__":
    sys.exit(main())
