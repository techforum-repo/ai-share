# Asset intake triage with the OpenAI Decisions API

A small, runnable example of the [OpenAI Decisions API](https://developers.openai.com/api/docs/guides/decisions) (public beta, `gpt-6-luna`) using image and text input together.

When an image arrives in a DAM with its title, alt text, and intended usage, the script decides whether the asset can **auto-approve**, needs **review** (and why), or should go **back to the uploader**:

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
asset_triage.py          the example: request builders, API calls (OpenAI SDK), routing policy
test_sample.py           tests: offline checks, plus live checks against /v1/decisions
fixtures/
  sample-asset.png       the sample image (an illustration of a hiker in a blue jacket)
  metadata.txt           its metadata, with alt text deliberately copied from a studio shot
  rights.txt             a rights note with a signed model release
  rights-missing.txt     a rights note with no release (negative control)
  sample-answers.json    ILLUSTRATIVE answers in the documented response shape, for offline tests
results/                 written by live runs (git-ignored)
```

The brand (Larkfield Outdoor), the asset, and the release number are fictional.

## Requirements

- Python 3.9+
- `test_sample.py` uses only the standard library, even in live mode.
- `asset_triage.py` calls the API through the OpenAI Python SDK, which needs a version with Decisions support (3.26.0 or later): `pip install -r requirements.txt`.
- For live calls: an `OPENAI_API_KEY` with access to the Decisions API.

## Test it

**Offline (no key):** checks the request shape, checks that the illustrative answers match the documented response shape, runs eight routing-policy cases, and checks that `triage()` makes the second request only when people are found.

```bash
python test_sample.py
# ... 43 passed, 0 warnings, 0 failed
```

**Live:** sends four cases to `POST /v1/decisions` and validates every answer. The cases are the sample, a control with correct alt text, the signed release, and the missing release. It then runs the sample end to end through the policy.

```bash
export OPENAI_API_KEY=sk-...
python test_sample.py --live                              # uses fixtures/sample-asset.png
python test_sample.py --live --image ~/photos/hiker.jpg --runs 3
```

- **FAIL:** the response breaks the documented shape. For example, an answer is missing, a probability is outside 0–1, a choice isn't one of yours, or a score is out of range. The exit code is 1.
- **WARN:** the model's judgement differs from the expectation. For example, a mismatched alt text should score below 0.5, and a missing release should score below 0.8. Expect some warnings; that is what you are measuring.
- **Output:** `results/live-run-<timestamp>.json` holds the raw responses, latency per call, and usage if returned. `results/live-answers.json` holds the sample's answers, which you can replay through the policy.

Tip: the sample image is an illustration, so the model may not treat its face as identifiable, and then the release check won't run. Use `--image` with a real web rendition (JPEG, PNG, or WebP, about 1000 px on the long side) for meaningful numbers.

## Run the example

```bash
# Print request 1 without calling the API (image data truncated)
python asset_triage.py fixtures/sample-asset.png fixtures/metadata.txt fixtures/rights.txt --dry-run

# Call the API through the SDK and print the route, reasons, and answers
python asset_triage.py fixtures/sample-asset.png fixtures/metadata.txt fixtures/rights.txt

# Run only the routing policy on saved answers
python asset_triage.py --replay fixtures/sample-answers.json
python asset_triage.py --replay results/live-answers.json
```

## Tuning

The thresholds at the top of `asset_triage.py` (`ALT_TEXT_MIN`, `THIRD_PARTY_MAX`, `RELEASE_MIN`, `READY_MIN`, `REJECT_BELOW`, `PEOPLE_MIN`) are starting points. Set them from labelled examples in your own library, based on the cost of each kind of error. A missed model release costs far more than one extra review.

## Notes

- **Beta.** `gpt-6-luna` is the only model, and request or response details may change before general availability.
- **Images.** They must be inline base64 data URLs. Hosted URLs and `file_id` are not supported, so send a downsized rendition, not the master.
- **Gates.** The example gates on predicates and the score, and treats the `choice` answer as metadata. Early community tests reported overconfident, order-sensitive choice probabilities.
- **Cost.** Pricing is per input token only ($0.10 per 1M at launch), and there is no input caching during the beta. Check the current pricing in the guide.
