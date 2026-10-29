# Kasparro visibility evaluation

- Mode: `live`
- Model: `qwen/qwen3.8-27b`
- Generated: `2026-10-29T11:34:31.469827+00:00`
- Label set: `v1`
- Supplied cases: 12 (8 tuning, 4 final-check)
- Controlled edge cases: 3 (reported separately)

## Coverage

| System | Eligible | Succeeded | Failed | Skipped | Label metric n |
|---|---:|---:|---:|---:|---:|
| baseline | 12 | 12 | 0 | 0 | 12 |
| approach | 12 | 12 | 0 | 0 | 12 |

Failed and skipped responses are excluded from label denominators and remain visible above.

## Core label metrics (supplied cases)

| System | Label | TP | FP | FN | TN | Precision | Recall | F1 | Kappa |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| baseline | kasparro_present | 12 | 0 | 0 | 0 | 1.0 | 1.0 | 1.0 | None |
| baseline | recommended | 3 | 3 | 0 | 6 | 0.5 | 1.0 | 0.6667 | 0.5 |
| baseline | negatively_characterised | 2 | 1 | 1 | 8 | 0.6667 | 0.6667 | 0.6667 | 0.5556 |
| approach | kasparro_present | 12 | 0 | 0 | 0 | 1.0 | 1.0 | 1.0 | None |
| approach | recommended | 3 | 0 | 0 | 9 | 1.0 | 1.0 | 1.0 | 1.0 |
| approach | negatively_characterised | 3 | 0 | 0 | 9 | 1.0 | 1.0 | 1.0 | 1.0 |

- Baseline macro F1: `0.7778`; raw agreement: `0.6667`
- Approach macro F1: `1.0`; raw agreement: `1.0`

## Agreement by engine and split

| System | Group | Value | n | Exact matches | Raw agreement |
|---|---|---|---:|---:|---:|
| baseline | by_engine | aimode | 4 | 3 | 0.75 |
| baseline | by_engine | chatgpt | 4 | 2 | 0.5 |
| baseline | by_engine | gemini | 4 | 3 | 0.75 |
| baseline | by_split | final_check | 4 | 3 | 0.75 |
| baseline | by_split | tuning | 8 | 5 | 0.625 |
| approach | by_engine | aimode | 4 | 4 | 1.0 |
| approach | by_engine | chatgpt | 4 | 4 | 1.0 |
| approach | by_engine | gemini | 4 | 4 | 1.0 |
| approach | by_split | final_check | 4 | 4 | 1.0 |
| approach | by_split | tuning | 8 | 8 | 1.0 |

## Evidence, citations, and competitors

| Check | Baseline | Approach |
|---|---:|---:|
| Evidence quote validity rate | n/a | 1.0 (25/25) |
| Competitor precision | 0.6 | 0.1111 |
| Competitor recall | 0.6 | 0.8 |
| Competitor F1 | 0.6 | 0.1951 |
| Citation-presence matches | 12/12 | same deterministic check |

Evidence validity is not claim support. It only checks that returned quotes occur verbatim in the answer. Citation presence is deterministic; source support was not inspected.

## Controlled edge cases

| Case | Expected | Approach status | Reason |
|---|---|---|---|
| edge-ambiguous-recommendation | succeeded | succeeded |  |
| edge-mixed-negative-praise | succeeded | succeeded |  |
| edge-evidence-boundary | failed | failed | invalid_evidence |

## Failures and limits

- Approach failures across all cases: 1 (expected controlled failures: 1).
- The sample is 12 supplied responses plus 3 controlled synthetic cases; estimates are unstable.
- One response per prompt and engine means no measure of run-to-run variability.
- ChatGPT used `force_web_search=true`; AI Mode and Gemini recorded null search settings, so engine comparisons are confounded.
- Replay mode demonstrates mechanics, not live-model quality, latency, or cost.
- Citation support is unverified; this evaluation measures citation presence only.
- The labels were produced by one annotator without a second adjudicator.
- The replay heuristic was revised after inspecting labelled cases; its replay agreement is not independent generalization evidence.
- The final-check split was not a fully blind holdout because the same author had inspected all 12 supplied answers while labelling.

## Reproduce

```bash
python -m eval.run --mode replay
```

Live mode requires the configured OpenAI-compatible endpoint and does not change the label set.
