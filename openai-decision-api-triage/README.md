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

2. **Request 2 (text only):** `release_confirmed` (predicate) reads the rights note together with the metadata, which holds the intended usage. It runs only when request 1 finds people, because it depends on an earlier answer.
3. **`route()`:** a plain-Python policy that holds every threshold. Refusals and uncertain cases go to a person.

## Contents

```
run.sh                   runs everything: the live test, then the example through the OpenAI SDK
asset_triage.py          the example: questions, requests, triage(), route(); calls the API via the OpenAI SDK
test_sample.py           live test: sends the sample and control cases to /v1/decisions and checks the answers
.env.example             copy to .env and add your OPENAI_API_KEY (.env is git-ignored)
fixtures/
  sample-asset.png       the sample image from the blog post (an illustration of a hiker in a blue jacket)
  metadata.txt           its metadata, with alt text deliberately copied from a studio shot
  rights.txt             a rights note with a signed model release
  rights-missing.txt     a rights note with no release
results/                 written by test runs (git-ignored)
```

The brand (Larkfield Outdoor), the asset, and the release number are fictional.

## Requirements

- Python 3.9+ and bash
- An `OPENAI_API_KEY` with access to the Decisions API
- `run.sh` creates `.venv/` and installs `requirements.txt` (OpenAI SDK 3.26.0 or later) for the SDK step. `test_sample.py` alone needs only the standard library.

## Run it

```bash
cp .env.example .env          # add your OPENAI_API_KEY
./run.sh                      # the sample image and its control cases
./run.sh --image ~/photos/hiker.jpg --alt "Hiker on a ridge at dusk" --runs 3
./run.sh --skip-sdk           # only the live test
```

`OPENAI_API_KEY` can also come from the environment instead of `.env`.

**Step 1, `test_sample.py`:** sends the sample with its wrong alt text, a control with the correct alt text (for the sample image, or when you pass `--alt`), a signed release note, and a note with no release. It validates every answer, then runs the sample end to end through `triage()`.

- **FAIL:** the response breaks the documented shape. For example, an answer is missing, a probability is outside 0–1, a choice isn't one of yours, or a score is out of range. The exit code is 1.
- **WARN:** the model's judgement differs from the expectation. For example, a mismatched alt text should score below 0.5, and a missing release should score below 0.8. Expect some warnings; they are what you are measuring.
- **Output:** `results/live-run-<timestamp>.json` holds the raw responses, latency per call, and token usage. `results/live-answers.json` holds the sample's final answers.

**Step 2, `asset_triage.py`:** runs the example itself through the OpenAI SDK, as you would in production, and saves its output to `results/sdk-run.json`.

For your own images, use a web rendition (JPEG, PNG, or WebP, about 1000 px on the long side), not the master file.

If the API returns an error, the script prints it. A 403 or 404 usually means the key or organisation doesn't have Decisions access yet.

## Run the example on your own assets

After one `./run.sh`, the virtual environment is ready:

```bash
.venv/bin/python asset_triage.py photo.jpg metadata.txt rights.txt
```

It prints the route, the reasons, and every answer. Write `metadata.txt` and `rights.txt` in the same format as the files in `fixtures/`.

## Next steps

1. Run `./run.sh` on a few real photos and read the warnings first.
2. Collect a few dozen assets where you already know the right outcome, and run them through `asset_triage.py`.
3. Tune the thresholds at the top of `asset_triage.py` (`ALT_TEXT_MIN`, `THIRD_PARTY_MAX`, `RELEASE_MIN`, `READY_MIN`, `REJECT_BELOW`, `PEOPLE_MIN`) based on what each kind of mistake costs you. A missed model release costs far more than one extra review.
4. Run it in shadow mode next to your current review process before letting it act.

## Notes

- **Beta.** `gpt-6-luna` is the only model, and request or response details may change before general availability.
- **Images.** They must be inline base64 data URLs. Hosted URLs and `file_id` are not supported, so send a downsized rendition, not the master.
- **Gates.** The example gates on predicates and the score, and treats the `choice` answer as metadata. Early community tests reported overconfident, order-sensitive choice probabilities.
- **Cost.** Pricing is per input token only ($0.10 per 1M at launch), and there is no input caching during the beta. Check the current pricing in the guide.
