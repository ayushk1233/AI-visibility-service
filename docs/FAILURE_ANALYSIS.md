# Failure, diagnosis, change, and regression

## Case

Supplied response `1abaa05d-1d41-4164-b554-1fc1cba01d79` (ChatGPT, prompt `kasparro:ai_shortlisting:004`).

- Human label: `kasparro_present=true`, `recommended=false`, `negatively_characterised=false`.
- Original replay output: `kasparro_present=true`, `recommended=true`, `negatively_characterised=false`.
- Why it matters: the answer gives generic contract/refund advice and offers to help interpret a plan. It does not present Kasparro as a suitable option, so scoring it as a recommendation would inflate the visibility index.

## Diagnosis

The replay heuristic joined every sentence containing "Kasparro" and searched for generic positive words. The offer sentence contains `"I can help determine..."`, which matched the `"can help"` cue. The same broad matching produced other controlled failures:

- `90b3eb79-...` was marked recommended because `"effectively"` matched `"effective"`, although the answer only described an audit.
- `0f217f49-...` was marked recommended because `"recommends"`, `"strong"`, and a table `"Yes"` appeared near Kasparro, although the answer preferred Profound.
- `f5109b67-...` was marked negative because generic `"no "` matched `"No agency can guarantee..."`, not a criticism of Kasparro.
- `95a7db89-...` was marked negative because `"not "` and `"missing"` matched `"not missing"`.
- `4abad2ab-...` was marked negative because `"doesn't"` matched a general statement about AI visibility tools.

The defect was not the LLM prompt or the scoring code. It was the replay stand-in using sentiment words instead of suitability claims.

## Change

`app/llm/replay.py` now:

- Uses suitability cues such as `"core use case"`, `"designed specifically"`, `"good option"`, and `"can help"` rather than generic `"yes"`, `"strong"`, `"effective"`, or table-cell sentiment.
- Excludes the speaker offer `"I can help"` from positive evidence.
- Treats `"not missing"` as a rebuttal, not a limitation.
- Uses specific negative phrases (`"not reliable"`, `"not yet"`, `"not publicly"`, `"overpriced"`, `"limited"`, etc.) rather than generic negations.

This is a replay-heuristic change. The live prompt already distinguishes a passing mention from a recommendation and treats answer text as untrusted.

## Regression

New tests in `tests/test_replay_regression.py` cover:

- A generic offer to help is not a recommendation.
- Explicit fit and core-use-case language are recommendations.
- Generic effectiveness and table `"Yes"` are not recommendations.
- `"not missing"` is not a negative characterisation.
- A material limitation such as `"not yet publicly"` is negative.

Replay evaluation before the change: approach macro F1 `0.7556`, raw agreement `0.4167`. After the change: macro F1 `1.0`, raw agreement `1.0` on the 12-case label set. Baseline is unchanged at macro F1 `0.7778`, raw agreement `0.6667`.

## Remaining uncertainty and disclosure

- The 12 cases are a small single-annotator sample. The perfect replay result is not evidence of live-model quality.
- The tuning/final-check split was created, but the author had already read all 12 answers while labelling. The final-check cases are therefore not a fully blind holdout, even though the rule changes were motivated by tuning errors. This limitation is repeated in the evaluation report and README.
- The controlled evidence-boundary edge case still fails intentionally with `invalid_evidence`; it is excluded from label metrics and reported separately.
- Replay remains a deterministic demonstration path, not a substitute for a live evaluation.
