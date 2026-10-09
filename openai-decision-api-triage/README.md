# Asset intake triage with the OpenAI Decisions API

A small, runnable example of the [OpenAI Decisions API](https://developers.openai.com/api/docs/guides/decisions) (public beta, `gpt-6-luna`) that uses image and text input together.

When an image arrives in a DAM with its title, alt text, and intended usage, the example decides whether the asset can **auto-approve**, needs **review** (and why), or should go **back to the uploader**:

1. **Request 1 (image + text):** five independent questions about the asset.

   | Question | Type | Used for |
   | --- | --- | --- |
   | `alt_text_matches` | predicate | Does the alt text describe the image? |
   | `identifiable_people` | predicate | Triggers the release check |
   | `third_party_mark` | predicate | Logos or watermarks that aren't the uploader's |
   | `asset_type` | choice | Metadata only, not a gate |
   | `publish_readiness` | score (0–2) | Unusable, Needs edits, or Ready |

2. **Request 2 (text only):** `release_confirmed` (predicate) reads the rights note. It runs only when request 1 finds people, because it depends on an earlier answer.
3. **`route()`:** a plain-Python policy that holds every threshold. Refusals and uncertain cases go to a person.

## Contents

```
asset_triage.py          the example: questions, requests, triage(), route(); calls the API via the OpenAI SDK
test_sample.py           live test: sends the sample and control cases to /v1/decisions and checks the answers
fixtures/
  sample-asset.png       the sample image (an illustration of a hiker in a blue jacket)
  metadata.txt           its metadata, with alt text deliberately copied from a studio shot
  rights.txt             a rights note with a signed model release
  rights-missing.txt     a rights note with no release
results/                 written by test runs (git-ignored)
```

The brand (Larkfield Outdoor), the asset, and the release number are fictional.

## Requirements

- Python 3.9+
- An `OPENAI_API_KEY` with access to the Decisions API
- `test_sample.py` needs only the standard library.
- `asset_triage.py` needs an OpenAI Python SDK with Decisions support (3.26.0 or later): `pip install -r requirements.txt`, ideally in a virtual environment.

## Test it against the API

```bash
export OPENAI_API_KEY=sk-...
python test_sample.py                                   # uses fixtures/sample-asset.png
python test_sample.py --image ~/photos/hiker.jpg --runs 3
```

It sends four cases (the sample with the wrong alt text, a control with the correct alt text, the signed release, and the missing release), validates every answer, and then runs the sample end to end through `triage()`.

- **FAIL:** the response breaks the documented shape. For example, an answer is missing, a probability is outside 0–1, a choice isn't one of yours, or a score is out of range. The exit code is 1.
- **WARN:** the model's judgement differs from the expectation. For example, a mismatched alt text should score below 0.5, and a missing release should score below 0.8. Expect some warnings; they are what you are measuring.
- **Output:** `results/live-run-<timestamp>.json` holds the raw responses, latency per call, and usage if returned. `results/live-answers.json` holds the sample's final answers.

Use `--image` with a real web rendition (JPEG, PNG, or WebP, about 1000 px on the long side). The sample image is an illustration, so the model may not treat its face as identifiable, and then the release check won't run.

If the API returns an error, the script prints it. A 403 or 404 usually means the key or organisation doesn't have Decisions access yet.

## Run the example on your own assets

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python asset_triage.py photo.jpg metadata.txt rights.txt
```

It prints the route, the reasons, and every answer. Write `metadata.txt` and `rights.txt` in the same format as the files in `fixtures/`.

## Next steps

1. Run the test on a few real photos and read the warnings first.
2. Collect a few dozen assets where you already know the right outcome, and run them through `asset_triage.py`.
3. Tune the thresholds at the top of `asset_triage.py` (`ALT_TEXT_MIN`, `THIRD_PARTY_MAX`, `RELEASE_MIN`, `READY_MIN`, `REJECT_BELOW`, `PEOPLE_MIN`) based on what each kind of mistake costs you. A missed model release costs far more than one extra review.
4. Run it in shadow mode next to your current review process before letting it act.

## Notes

- **Beta.** `gpt-6-luna` is the only model, and request or response details may change before general availability.
- **Images.** They must be inline base64 data URLs. Hosted URLs and `file_id` are not supported, so send a downsized rendition, not the master.
- **Gates.** The example gates on predicates and the score, and treats the `choice` answer as metadata. Early community tests reported overconfident, order-sensitive choice probabilities.
- **Cost.** Pricing is per input token only ($0.10 per 1M at launch), and there is no input caching during the beta. Check the current pricing in the guide.
