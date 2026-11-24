# Pinned τ-bench retail source and read-only probes

Upstream: [Sierra Research τ-bench](https://github.com/sierra-research/tau2-bench),
release [v2.0.0](https://github.com/sierra-research/tau2-bench/releases/tag/v2.0.0),
commit `989d3285ae669e49503324bdb9981bf14fccedeb`.
The τ³-bench release is part of this same repository's history; there is no
separate `tau3-bench` repository used here. The pinned 2.0 release is newer.

Files under `source/` are byte-for-byte upstream snapshots. Their original
paths, SHA-256 digests and sizes are in [source/manifest.json](source/manifest.json).
They are distributed under the upstream [MIT license](source/LICENSE),
Copyright (c) 2025 Sierra Research. This folder preserves that notice.
No model calls or network download are needed to verify the snapshot.

## Compatibility and scope

The current MAS is read-only. Native retail tasks require confirmed writes,
exchanges, address/payment edits, product variants, multiple requests and
email or name-plus-zip authentication. This project has different return rules,
identifiers and schemas. The original action tasks are therefore not evaluated
as supported tasks here, and a status answer is not credited as completing one.

The branch instead runs **new status questions using source database facts**.
Original reference trajectories index orders for probe construction; their
action requirements and user simulations are not executed. This is a narrower
generalization check of the existing MAS, not a native benchmark run.

| Source set | Source tasks | Source tasks linked to probes | Unique source-order probes | Synthetic controls | Total probes |
|---|---:|---:|---:|---:|---:|
| `reviewed50` (default) | 50 | 49 | 73 | 3 | 76 |
| `legacy114` | 114 | 111 | 115 | 3 | 118 |

2.0 includes 50 reviewed English task replacements; the older/base 114-task
source file is also included in the release. Review and task fixes do not
guarantee an ambiguity-free or error-free dataset. Original 1.x and 2.0 scores
are not directly comparable, and these adapted DeepEval probe scores are
comparable to neither leaderboard.

Order probes are deduplicated by order ID; every contributing source task ID is
retained. Tasks with no valid referenced order appear explicitly in the audit.
Multiple order references in one source task can yield multiple probes. Some
references deliberately contain wrong IDs for correction scenarios; these are
reported as unavailable references, not automatically classified as data errors
and not silently corrected into positive examples.

## What runs

Each status probe sends a new question such as `What is the status of order
#W1267569?`, then supplies that order owner's source email only when the MAS
asks for identification. This email is added to the adapted conversation even
when the original simulated customer did not remember it. Thus original
name/zip recovery and user behavior are outside the measured scope.

The real supervisor, identity and order nodes, Ollama models, graph checkpoints
and read-only tools run normally. The adapter supplies a temporary fixture
containing only source names/emails, ownership, order IDs and recorded statuses.
No dates, membership tiers, return windows or ETA facts are invented. Native
`processed` stays `processed`; it is not silently renamed to this project's
`processing` state. Source product, payment and address records are not exposed
through this narrow projection. It does not test those capabilities.

Three extra controls exercise cross-owner denial, unknown-order denial and
unknown-email correction. They are labeled synthetic and have no claim to be
native tasks. Corrections and the source `#W...` identifier format preserve
the original request across the human interruption.

The application fixture and default data are restored after each probe. The
external profile cannot retrieve this project's return policy, so a mistaken
return route fails instead of applying retail-v2 rules to benchmark orders.
Gold expected answers and original action trajectories are not model inputs.
Full source policy is available to the judge as reference only.

DeepEval scores **Task Completion and Answer Correctness**, using the existing
0.8 thresholds and shared runner. Independent checks cover identity, ownership,
source status, expected stop, allowed tools and fixture immutability. A judge
pass cannot override a failed check. CLI failures and partial runs remain visible.
Protocol is `tau-status-probes-v1`; every run states that native end-to-end tasks
evaluated is **zero**. No Matrace collection or separate Python environment is
introduced.

## Run locally

Use the root environment and existing Ollama tags (`qwen3:4b`, `qwen3:0.6b`, and
the judge `llama3.1:8b`). From the repository root:

```bash
uv sync --frozen
# Offline source validation and coverage:
uv run --frozen python Evaluations/tau_retail/evaluate.py --audit
uv run --frozen python Evaluations/tau_retail/evaluate.py --list --limit 5
# First smoke test, then all 76 probes from the reviewed source set:
uv run --frozen python Evaluations/tau_retail/evaluate.py --limit 3
uv run --frozen python Evaluations/tau_retail/evaluate.py --model llama3.1:8b
# Explicit controls:
uv run --frozen python Evaluations/tau_retail/evaluate.py --case TAU-CROSS-OWNER --case TAU-UNKNOWN-ORDER --case TAU-EMAIL-CORRECTION
# Larger legacy source set, 118 derived probes:
uv run --frozen python Evaluations/tau_retail/evaluate.py --task-set legacy114
```

`--model qwen3:4b` selects the installed 4B judge. Judge quality has not been
benchmarked here. Inspect reasons manually before deciding which model works
better. Save probe results separately from the existing 25-task reports.

Tests validate source checksums, deduplication, all 118 legacy-derived probes
through the actual graph/tools with controlled models, fixture isolation,
incorrect source-status detection, policy isolation and native DeepEval calls.
These are integration checks, not measured live Ollama benchmark results.

## A later full benchmark integration

Native τ-bench completion requires its own user simulator, source policy/tools,
confirmed transactional operations, fresh database state, and native reward
evaluation. Retail's reference actions generally derive a target database state;
they are not an exact required tool sequence. DeepEval can be supplementary,
but cannot replace the database end-state and applicable assertion checks while
retaining official benchmark meaning. See upstream's pinned
[evaluation documentation](https://github.com/sierra-research/tau2-bench/blob/989d3285ae669e49503324bdb9981bf14fccedeb/docs/evaluation.md).
That extension is deliberately outside this branch's read-only scope.
