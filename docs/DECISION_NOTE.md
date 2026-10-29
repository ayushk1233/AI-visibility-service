# Decision note

## Product decision

The service answers a narrow question: in this archived snapshot, where is Kasparro included, recommended, or negatively described, what evidence supports that view, and what should be investigated next? It does not claim to measure live rankings or causal impact.

## Scoring

The Visibility Index is deterministic:

`100 * (0.40 * inclusion_rate + 0.40 * recommendation_rate + 0.20 * non_negative_rate)`

- A passing mention is not a recommendation; recommendation requires presence.
- Negative characterisation may overlap with recommendation.
- Failed and skipped responses are excluded from label denominators but remain in coverage, failure, and skip counts.
- Wilson 95% intervals are reported for each core rate.
- `n=0` returns null rates and a null index, never zero.
- The response-level labels, weights, taxonomy, prompt version, and category taxonomy are hashed into the analysis config. Same inputs and config produce byte-identical scores.

## Backend and review

FastAPI plus stdlib SQLite was chosen because the supplied volume is 150 responses and the important problem is transaction correctness, not scale. The schema has eight core tables plus an append-only audit log (nine total).

- Analyst and reviewer identities come only from server-side bearer-token comparison; caller-supplied role headers are ignored.
- Drafts are editable in place with optimistic `version_no` checks.
- Submitting freezes the content and starts a review clock.
- Approval/rejection is a conditional compare-and-set update, so repeated or concurrent decisions produce one winner and a 409 conflict for the loser.
- Editing after submission creates a new draft; it never authorises the old approved content.
- Export rechecks state, expiry, explicit version identity, analysis config hash, stored content hash, and the deterministic evidence audit.
- A bounded database lease makes interrupted analysis work resumable without duplicating completed results.
- Raw model output, attempt traces, token counts, latency, and estimated cost are stored for each result.

## Alternatives rejected

- ORM or migration framework: unnecessary for nine tables and would obscure the SQL constraints that enforce review safety.
- Dashboard: the OpenAPI docs and JSON API are sufficient for the assignment.
- Queue service or multi-process worker: SQLite and one in-process worker keep the demo reproducible; the lease and compare-and-set logic still demonstrate restart and concurrency safety.
- Vector search, crawling, or live provider collection: out of scope and would add unverified claims.
- OAuth: two local roles are enough for the review gate; secrets stay in environment variables.

## Top remedy

The first remedy is to investigate the lowest-inclusion engine and its retrieved sources for the same prompts, then publish crawlable, evidence-backed pages for the missing use cases. It is prioritised first because inclusion is a prerequisite for recommendation. The engine comparison is explicitly confounded by ChatGPT `force_web_search=true` and null search settings for AI Mode and Gemini, so the remedy is an investigation, not a claim that one engine is permanently weaker.

## Failure and fix

A replay failure marked a generic "I can help determine..." offer as a recommendation. The diagnosis was overly broad sentiment matching. The fix replaced generic sentiment cues with suitability cues, excluded speaker offers, and added specific negative phrases. The regression tests and before/after metrics are in `docs/FAILURE_ANALYSIS.md`.

## Risks and omissions

- No live-model evaluation was run. Replay metrics demonstrate mechanics and controlled failure handling, not model quality.
- The 12-case label set is small, single-annotator, and not a fully blind holdout.
- Citation presence is measured; citation support is unverified.
- The pack checker still fails on the known `Assignment.docx` / `Assignment.pdf` manifest filename mismatch. Inputs were not renamed or edited.
- No multipart upload, dashboard, migration framework, or multi-process worker was built.
- Review TTL defaults to 24 hours; tests use explicit short/past timestamps rather than sleeping.

## Active time

Active time was not instrumented. Based on the session and file timestamps, implementation, tests, evaluation, and documentation were completed in one working session; this is an estimate rather than a precise time log. The six-hour cap was respected, but the remaining work is disclosed above rather than hidden.

## AI assistance

An AI coding assistant was used to draft and implement the service, tests, evaluation harness, and documentation. The human owner remains responsible for reviewing the code, labels, and claims, and for rerunning the tests and evaluation before submission.
