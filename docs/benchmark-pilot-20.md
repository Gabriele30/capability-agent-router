# CAR 20-Task Pilot Benchmark

## Executive summary

On this internal 20-task pilot, repeated three times per strategy, CAR had a
similar pooled verified-success rate to Codex-only (85.0% versus 83.3%) while
using 37.9% less reference API-equivalent cost per verified success and 33.1%
less mean latency. This is an observational pilot result, not a statistical
significance claim or a claim of universal superiority.

## Experimental question

Can CAR preserve a similar verified outcome rate to a Codex-only baseline while
using lower reference inference cost and latency through capability-aware routing?

## Configuration

| Item | Value |
| --- | --- |
| CAR version | 0.6.0 |
| Current development commit | `7005085d02b2b81622f08757779ac900c39ed06b` |
| Cheap tier | `gemini-3.5-flash-lite` (minimal thinking level) |
| Power tier | `gpt-5.6-terra` |
| Codex reasoning effort | `medium` |
| Verification | trusted CAR-owned hidden oracle |
| Cost basis | reference API-equivalent public list price |
| Price snapshot verified | 2026-08-11 |

Reference inference cost is not actual provider billing or ChatGPT/Codex billing.

## Benchmark design

The pilot contains 20 unique synthetic, deterministic Python tasks: 5
trivial/localized, 5 normal bugfix/edge-case, 5 multi-file/moderate-reasoning,
and 5 cross-file/harder-reasoning tasks. Every strategy receives the same task
contract, explicit case-specific authorized paths, clean Git baseline and trusted
hidden verification oracle. Visible development tests may be authorized for a
case; the hidden oracle is never provider-visible or writable.

CAR starts from a fresh baseline, validates the structured proposal, and accepts
a result only after hidden verification. Reference cost includes failed attempts
when usage is complete.

## Verification model

The hidden oracle is CAR-owned verification infrastructure rather than a
provider-authored test. Its contents are intentionally not published here.

## Manifest identity

Result metadata records
`2ea1f7ff02af50c1e73dcff614deb01e682f3de8fdfec4f7db5c8668ff446fb3`.
This is not the SHA-256 of raw JSON file bytes: CAR hashes
`BenchmarkManifest.model_dump(mode="json")` serialized with sorted keys and
compact separators. It is a canonical semantic-model hash, including normalized
defaults. The local raw-file SHA-256 was
`87b159a963980678ed0c5a6fef27d5ee5390a4ea086faf52ae478d949797da2e`.

## Per-run results

### Codex-only

| Run | Verified success | Reference cost | Cost / success | Mean latency | Unknown cost |
| --- | ---: | ---: | ---: | ---: | ---: |
| 1 | 18 / 20 (90%) | $1.0893840 | $0.06052133 | 40,806.65 ms | 0 |
| 2 | 15 / 20 (75%) | $0.8601515 | $0.05734343 | 38,097.60 ms | 0 |
| 3 | 17 / 20 (85%) | $0.8434205 | $0.04961297 | 32,211.50 ms | 0 |

Run 1 was produced after hunk-count canonicalization at `ec0d5b4`, before the
Gemini tier switch. The later Gemini-tier change did not modify Codex runtime or
configuration; this commit distinction is retained as a limitation.

### CAR

| Run | Verified success | Reference cost | Cost / success | Mean latency | Codex attempts | Avoidance |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 18 / 20 (90%) | $0.6605457 | $0.03669698 | 24,594.70 ms | 11 / 20 | 9 / 20 |
| 2 | 17 / 20 (85%) | $0.5461752 | $0.03212795 | 25,134.40 ms | 12 / 20 | 8 / 20 |
| 3 | 16 / 20 (80%) | $0.5636657 | $0.03522911 | 24,583.75 ms | 12 / 20 | 8 / 20 |

The summary field named `codex_escalation_count` counts cases with a controlled
Codex attempt, including direct Codex routing; it is not exclusively fallback
escalation. This report therefore calls it **Codex attempts**.

## Aggregated CAR vs Codex-only results

These are 60 strategy executions over 20 fixed tasks, not 60 independent tasks.

| Metric | Codex-only | CAR |
| --- | ---: | ---: |
| Verified successes | 50 / 60 | 51 / 60 |
| Pooled verified-success rate | 83.33% | 85.00% |
| Total reference cost | $2.7929560 | $1.7703866 |
| Reference cost / verified success | $0.05585912 | $0.03471346 |
| Weighted mean latency | 37,038.58 ms | 24,770.95 ms |

CAR's pooled success-rate difference was **+1.67 percentage points**. Relative
to Codex-only, total reference cost was **36.61% lower**, reference cost per
verified success **37.86% lower**, and mean latency **33.12% lower**. Per run,
CAR tied once, had higher verified success once, and lower verified success once.

## Gemini-only reference run

One clean, cost-complete `gemini-3.5-flash-lite` run is reported separately:

| Verified success | Reference cost | Cost / success | Mean | Median | Unknown cost |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 8 / 20 (40%) | $0.0264368 | $0.0033046 | 3,147.25 ms | 3,133 ms | 0 |

This is a low-cost, low-latency reference tier; it does not establish Gemini-only
as sufficient for the whole task set.

## Routing behavior

CAR's intended mechanism is cheap capability first where appropriate, trusted
verification when it is sufficient, and Codex use where required. The results
are consistent with this mechanism, but routing is not claimed to be optimal.

## Economics interpretation

The comparison uses reference API-equivalent public list prices, not actual
billing. The aggregate includes complete failed attempts. Unknown usage is not
treated as zero cost.

## Excluded and contaminated runs

Gemini-only repetitions 2 and 3 each had HTTP 429 contamination, 7/20 verified
successes, and one unknown-cost result. They are excluded from Gemini quality and
economics aggregation. Observed project free-tier limits were 15 RPM, 250K TPM
and 500 RPD. The evidence does not establish which quota dimension caused 429.

## Limitations

- Only 20 unique, synthetic/internal tasks were used; the same tasks were
  repeated three times per strategy.
- Models are stochastic; no p-values or statistical-significance claims are made.
- One Codex-only repetition used an earlier development commit, although the
  intervening Gemini-tier change did not alter Codex runtime/configuration.
- Gemini-only repetitions 2 and 3 were excluded because of HTTP 429 contamination.
- Reference API-equivalent pricing is not actual ChatGPT, Codex, or provider billing.
- Free-tier quotas are not production service limits.
- The pilot does not establish behavior on large real-world repositories or an
  external unseen benchmark set.
- Fixtures are local and intentionally untracked. A fresh clone cannot yet
  reproduce this dataset; this is evidence documentation, not a published dataset.

## Reproduction notes

The following are explicit live operations and were not run for this report:

```text
car benchmark benchmarks/pilot-smoke/manifest.json --strategy gemini-only

car benchmark benchmarks/pilot-smoke/manifest.json --strategy codex-only \
  --codex-model gpt-5.6-terra --codex-effort medium

car benchmark benchmarks/pilot-smoke/manifest.json --strategy car \
  --codex-model gpt-5.6-terra --codex-effort medium
```

Use normal shell continuation syntax when splitting commands. Preserve the
canonical semantic manifest hash in result metadata and do not mix results
from different model configurations numerically.

## Next validation step

Freeze the current routing, use a larger benchmark with preferably
external/unseen tasks, and test whether the observed cost/success relationship
persists. This report does not begin that work.
