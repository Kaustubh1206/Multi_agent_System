# Review of Results_Thinking.md

Source: [Results_Thinking.md](../../Results_Thinking.md), added in commit `1945425`.
The original output and the user's saved artifacts are preserved.
Run: `20261126T204957955653Z`. Configuration confirmed from the saved manifest:
supervisor `qwen3:4b`, specialists `qwen3:0.6b`, simulator/judge `llama3.1:8b`,
context 32768, temperature zero in the adapter.

## What the 98 recorded cases show

| Measure | Recorded result |
| --- | ---: |
| Cases recorded | 98/98 |
| Combined passes | 0/98 |
| Native end-state passes | 0/98 |
| Failed / incomplete | 89 / 9 |
| Task Completion | 32/98 pass; mean 0.636 |
| Answer Correctness | 7/98 pass; mean 0.302 |
| Recorded per-case runtime/judge errors | 0 |

Both DeepEval metrics passed on cases 8, 16, 24, 60, 75, 85 and 87, but the
native checks still failed in every case. These are judge scores, not native task
success rates. All tasks were attempted; `complete: false` reflects the nine
incomplete conversations rather than missing cases.

Every one of the **188 displayed routing decisions selected identity**. Eighty-nine
cases have one routing decision; nine have eleven. The incomplete cases are:

- 9: ST004_004
- 17: ST006_002
- 28: ST006_019
- 30: ST006_022
- 36: ST006_030
- 42: ST006_039
- 54: ST007_008
- 61: ST011_006
- 79: ST017_003

This shows routing/progress problems, including identity routes whose next-step
text names order lookups, policy work or installation work. The pasted output
alone does not establish the actual tool-call count for every case or each
incomplete case's exact native termination reason.

## First case: confirmed against the saved conversation

The user supplied the first native artifact's visible conversation and checker
failure. It confirms **zero observed tool calls** and termination `done`. The
assistant repeated the supplied shipment details, invented a delay explanation
based on the order being placed 18 days earlier, stated that no compensation
eligibility was mentioned, referred the customer back to TechHome Direct, and
emitted `<DONE>`.

The native checker reports five actual Zendesk tickets versus six expected,
with the new ticket missing. This directly explains a native failure without
assuming that any refund, lookup or ticket update happened.

The pinned fixture's order total is $435. The retrieved delayed-delivery policy
requires an order total above $500 as well as the qualifying delay for the
specified compensation. The reference writes for this first case create and
resolve a ticket; they do not create a refund. A correct supported denial can be
appropriate here. The actor still needed to retrieve the relevant facts/rules and
complete ticket handling. Frustration alone does not establish refund eligibility.
The judge's suggestion that a refund should have been offered is not reliable
policy evidence.

Do not infer that all 98 tasks had zero tool calls from this one artifact.
The other cases require their saved operations and database differences to
identify exactly which actions failed.

## Separate evaluator problems

1. **Reference actions could be mistaken for executed actions.** The previous
   Task Completion input included source policy and native golden database diffs
   inside its observed-workflow trace. Expected data must not establish that an
   agent actually called a tool. Several reasons claim actions succeeded despite
   native failure; the first case's saved zero-tool conversation confirms that
   such claims need independent verification.
2. **Control markers were treated as answer content.** Sixty-six Answer
   Correctness reasons mention `<DONE>`. At least 41 explicitly quote the actual
   output as only that marker. These counts are descriptions in judge reasons,
   not a substitute for the raw answer artifacts. A real marker-only reply is
   empty; a valid trailing marker on a substantive answer is not a wording error.
3. **The judge sometimes invents requirements or facts.** Case 23's Task
   Completion reason mentions flight/hotel and presentation work, which is not
   available in the enabled retail toolset. Case 24's Answer Correctness reason
   compares apostrophe variants as if the reference were a verbatim answer.
   Case 8 criticizes non-verbatim extra wording despite scoring it highly.

The model's reported reasoning should therefore not be used as an unquestioned
failure label. Native tool results and database checks remain independent evidence.
The 0.6B specialist's adequacy and the 4B router's model-specific behavior have not
been isolated by a controlled live comparison. An 8B comparison is useful, but
changing models alone does not demonstrate a fix.

## Changes made after this review

Protocol: **thinkingbox-retail-mas-v3**. Source tasks, tools, policies, native
checks and thresholds remain unchanged. The original 25-task application is
unchanged. No task-specific golden answers were added to agent prompts.

- The router receives an explicit classification request, with source policy,
  available tools and observed history labeled as input data. Its prompt separates
  identity work from order and policy work.
- Ollama requests consolidate trusted system instructions into one leading
  system message, preserving the source policy and node instructions. This is
  prompt-delivery hardening; the report does not prove that a particular Ollama
  template was the sole cause of the earlier routing behavior.
- Specialist prompts require actual tool use, source ticket lifecycle and a
  substantive customer answer. A marker-only reply, a final reply before any
  tool result, or `<DONE>` while asking for information/calling a tool triggers
  one bounded pre-execution repair. Persistent invalid output is recorded as an
  error, not turned into a fabricated answer or a passing score. Rejected proposals
  and repair reasons stay in the routing metadata. Writes are never replayed.
- The Task Completion outcome extractor receives only actual conversation and
  actual operations, without reference diffs, golden hashes or policy instructions
  masquerading as completed steps. Source policy and reference requirements stay
  separately labeled as evaluation requirements.
- The final trailing `<DONE>` marker is removed only from DeepEval's visible-text
  representation; native artifacts retain the original protocol. Empty substantive
  answers receive an explicit zero correctness result labeled `empty_answer_check`,
  without asking a judge to invent a score. Other answers use native GEval.
- Console and JSON case records now include the finish reason, actual tool count,
  tool names and final customer text, so a rerun exposes execution failures directly.

Scores from v2 and v3 are not a controlled measure of prompt improvement: actor
and evaluator inputs changed. Always compare coverage, errors, native checks and
actual operations alongside DeepEval means. No live post-change improvement is
claimed from controlled tests.

## Rerun a smoke case before the whole suite

With Ollama running, from the repository root:

```bash
git switch ThinkingBox
git pull --ff-only
uv run --frozen --extra thinkingbox python Evaluations/thinkingbox/run.py --limit 1
```

Inspect the new `observed_tool_calls`, `tool_names`, `final_answer` and native
result. The first task should retrieve facts and satisfy the source's ticket
workflow rather than answer entirely from the user's opening sentence. A correct
result does not require issuing a refund for this case.

For an explicitly labeled specialist comparison on the same case:

```bash
uv run --frozen --extra thinkingbox python Evaluations/thinkingbox/run.py --limit 1 --specialist-model llama3.1:8b
```

After inspecting that behavior, run the default command without `--limit` for all
98 retail tasks. Judge reliability still needs human spot checks; a favorable
mean alone is insufficient.

## Inventory of every recorded case

Native checks failed in all rows. TC/AC are the displayed judge scores, not
independently corrected labels. Case links point to the original pasted output.

| # | Task | TC | AC | Recorded status | Router decisions |
| ---: | --- | ---: | ---: | --- | ---: |
| 1 | [ST002_001](../../Results_Thinking.md#L5) | 0.6 | 0.6 | failed | 1 |
| 2 | [ST002_003](../../Results_Thinking.md#L8) | 0.7 | 0.2 | failed | 1 |
| 3 | [ST002_004](../../Results_Thinking.md#L11) | 0.8 | 0.0 | failed | 1 |
| 4 | [ST003_003](../../Results_Thinking.md#L14) | 0.6 | 0.2 | failed | 1 |
| 5 | [ST003_004](../../Results_Thinking.md#L17) | 0.7 | 0.6 | failed | 1 |
| 6 | [ST003_006](../../Results_Thinking.md#L20) | 0.8 | 0.6 | failed | 1 |
| 7 | [ST004_001](../../Results_Thinking.md#L23) | 0.6 | 0.2 | failed | 1 |
| 8 | [ST004_003](../../Results_Thinking.md#L26) | 0.9 | 0.9 | failed | 1 |
| 9 | [ST004_004](../../Results_Thinking.md#L29) | 0.6 | 0.6 | incomplete | 11 |
| 10 | [ST004_005](../../Results_Thinking.md#L42) | 0.7 | 0.2 | failed | 1 |
| 11 | [ST004_007](../../Results_Thinking.md#L45) | 0.6 | 0.6 | failed | 1 |
| 12 | [ST004_008](../../Results_Thinking.md#L48) | 0.6 | 0.2 | failed | 1 |
| 13 | [ST004_009](../../Results_Thinking.md#L51) | 0.7 | 0.4 | failed | 1 |
| 14 | [ST005_001](../../Results_Thinking.md#L54) | 0.6 | 0.2 | failed | 1 |
| 15 | [ST005_005](../../Results_Thinking.md#L57) | 0.6 | 0.2 | failed | 1 |
| 16 | [ST006_001](../../Results_Thinking.md#L60) | 0.9 | 0.8 | failed | 1 |
| 17 | [ST006_002](../../Results_Thinking.md#L63) | 0.6 | 0.2 | incomplete | 11 |
| 18 | [ST006_003](../../Results_Thinking.md#L76) | 0.6 | 0.4 | failed | 1 |
| 19 | [ST006_004](../../Results_Thinking.md#L79) | 0.6 | 0.7 | failed | 1 |
| 20 | [ST006_005](../../Results_Thinking.md#L82) | 0.6 | 0.2 | failed | 1 |
| 21 | [ST006_006](../../Results_Thinking.md#L85) | 0.6 | 0.2 | failed | 1 |
| 22 | [ST006_008](../../Results_Thinking.md#L88) | 0.7 | 0.7 | failed | 1 |
| 23 | [ST006_010](../../Results_Thinking.md#L91) | 0.0 | 0.2 | failed | 1 |
| 24 | [ST006_011](../../Results_Thinking.md#L94) | 0.9 | 0.9 | failed | 1 |
| 25 | [ST006_015](../../Results_Thinking.md#L97) | 0.7 | 0.7 | failed | 1 |
| 26 | [ST006_016](../../Results_Thinking.md#L100) | 0.6 | 0.2 | failed | 1 |
| 27 | [ST006_017](../../Results_Thinking.md#L103) | 0.8 | 0.2 | failed | 1 |
| 28 | [ST006_019](../../Results_Thinking.md#L106) | 0.0 | 0.2 | incomplete | 11 |
| 29 | [ST006_021](../../Results_Thinking.md#L119) | 0.6 | 0.0 | failed | 1 |
| 30 | [ST006_022](../../Results_Thinking.md#L122) | 0.6 | 0.4 | incomplete | 11 |
| 31 | [ST006_023](../../Results_Thinking.md#L135) | 0.7 | 0.2 | failed | 1 |
| 32 | [ST006_024](../../Results_Thinking.md#L138) | 0.6 | 0.6 | failed | 1 |
| 33 | [ST006_025](../../Results_Thinking.md#L141) | 0.2 | 0.2 | failed | 1 |
| 34 | [ST006_026](../../Results_Thinking.md#L144) | 0.2 | 0.2 | failed | 1 |
| 35 | [ST006_028](../../Results_Thinking.md#L147) | 0.9 | 0.2 | failed | 1 |
| 36 | [ST006_030](../../Results_Thinking.md#L150) | 0.7 | 0.0 | incomplete | 11 |
| 37 | [ST006_031](../../Results_Thinking.md#L163) | 0.8 | 0.2 | failed | 1 |
| 38 | [ST006_032](../../Results_Thinking.md#L166) | 0.8 | 0.2 | failed | 1 |
| 39 | [ST006_033](../../Results_Thinking.md#L169) | 0.7 | 0.0 | failed | 1 |
| 40 | [ST006_034](../../Results_Thinking.md#L172) | 0.8 | 0.6 | failed | 1 |
| 41 | [ST006_037](../../Results_Thinking.md#L175) | 0.7 | 0.0 | failed | 1 |
| 42 | [ST006_039](../../Results_Thinking.md#L178) | 0.6 | 0.2 | incomplete | 11 |
| 43 | [ST006_040](../../Results_Thinking.md#L191) | 0.9 | 0.0 | failed | 1 |
| 44 | [ST006_041](../../Results_Thinking.md#L194) | 0.2 | 0.2 | failed | 1 |
| 45 | [ST006_042](../../Results_Thinking.md#L197) | 0.6 | 0.2 | failed | 1 |
| 46 | [ST006_043](../../Results_Thinking.md#L200) | 0.6 | 0.2 | failed | 1 |
| 47 | [ST006_044](../../Results_Thinking.md#L203) | 0.8 | 0.2 | failed | 1 |
| 48 | [ST006_045](../../Results_Thinking.md#L206) | 0.6 | 0.0 | failed | 1 |
| 49 | [ST006_047](../../Results_Thinking.md#L209) | 0.6 | 0.0 | failed | 1 |
| 50 | [ST006_049](../../Results_Thinking.md#L212) | 0.2 | 0.0 | failed | 1 |
| 51 | [ST007_003](../../Results_Thinking.md#L215) | 0.6 | 0.2 | failed | 1 |
| 52 | [ST007_005](../../Results_Thinking.md#L218) | 0.2 | 0.2 | failed | 1 |
| 53 | [ST007_006](../../Results_Thinking.md#L221) | 0.9 | 0.6 | failed | 1 |
| 54 | [ST007_008](../../Results_Thinking.md#L224) | 0.4 | 0.2 | incomplete | 11 |
| 55 | [ST008_003](../../Results_Thinking.md#L237) | 0.6 | 0.0 | failed | 1 |
| 56 | [ST009_001](../../Results_Thinking.md#L240) | 0.8 | 0.0 | failed | 1 |
| 57 | [ST009_004](../../Results_Thinking.md#L243) | 0.7 | 0.6 | failed | 1 |
| 58 | [ST009_005](../../Results_Thinking.md#L246) | 0.7 | 0.6 | failed | 1 |
| 59 | [ST010_001](../../Results_Thinking.md#L249) | 0.9 | 0.6 | failed | 1 |
| 60 | [ST011_002](../../Results_Thinking.md#L252) | 0.8 | 0.8 | failed | 1 |
| 61 | [ST011_006](../../Results_Thinking.md#L255) | 0.6 | 0.2 | incomplete | 11 |
| 62 | [ST011_007](../../Results_Thinking.md#L268) | 0.8 | 0.6 | failed | 1 |
| 63 | [ST011_009](../../Results_Thinking.md#L271) | 0.9 | 0.0 | failed | 1 |
| 64 | [ST012_003](../../Results_Thinking.md#L274) | 0.9 | 0.2 | failed | 1 |
| 65 | [ST013_001](../../Results_Thinking.md#L277) | 0.2 | 0.2 | failed | 1 |
| 66 | [ST013_003](../../Results_Thinking.md#L280) | 0.7 | 0.2 | failed | 1 |
| 67 | [ST013_004](../../Results_Thinking.md#L283) | 0.6 | 0.4 | failed | 1 |
| 68 | [ST013_005](../../Results_Thinking.md#L286) | 0.2 | 0.2 | failed | 1 |
| 69 | [ST013_006](../../Results_Thinking.md#L289) | 0.6 | 0.4 | failed | 1 |
| 70 | [ST013_007](../../Results_Thinking.md#L292) | 0.8 | 0.6 | failed | 1 |
| 71 | [ST013_008](../../Results_Thinking.md#L295) | 0.9 | 0.7 | failed | 1 |
| 72 | [ST014_002](../../Results_Thinking.md#L298) | 0.2 | 0.6 | failed | 1 |
| 73 | [ST014_005](../../Results_Thinking.md#L301) | 0.6 | 0.0 | failed | 1 |
| 74 | [ST015_001](../../Results_Thinking.md#L304) | 0.9 | 0.0 | failed | 1 |
| 75 | [ST016_004](../../Results_Thinking.md#L307) | 0.9 | 0.8 | failed | 1 |
| 76 | [ST016_006](../../Results_Thinking.md#L310) | 0.7 | 0.6 | failed | 1 |
| 77 | [ST017_001](../../Results_Thinking.md#L313) | 0.9 | 0.2 | failed | 1 |
| 78 | [ST017_002](../../Results_Thinking.md#L316) | 0.8 | 0.2 | failed | 1 |
| 79 | [ST017_003](../../Results_Thinking.md#L319) | 0.2 | 0.0 | incomplete | 11 |
| 80 | [ST017_005](../../Results_Thinking.md#L332) | 0.6 | 0.2 | failed | 1 |
| 81 | [ST018_002](../../Results_Thinking.md#L335) | 0.2 | 0.2 | failed | 1 |
| 82 | [ST018_003](../../Results_Thinking.md#L338) | 0.9 | 0.0 | failed | 1 |
| 83 | [ST018_005](../../Results_Thinking.md#L341) | 0.6 | 0.2 | failed | 1 |
| 84 | [ST025_001](../../Results_Thinking.md#L344) | 0.6 | 0.2 | failed | 1 |
| 85 | [ST025_002](../../Results_Thinking.md#L347) | 0.9 | 0.8 | failed | 1 |
| 86 | [ST025_003](../../Results_Thinking.md#L350) | 0.6 | 0.2 | failed | 1 |
| 87 | [ST025_004](../../Results_Thinking.md#L353) | 0.9 | 0.8 | failed | 1 |
| 88 | [ST025_006](../../Results_Thinking.md#L356) | 0.9 | 0.0 | failed | 1 |
| 89 | [ST026_002](../../Results_Thinking.md#L359) | 0.6 | 0.2 | failed | 1 |
| 90 | [ST026_003](../../Results_Thinking.md#L362) | 0.6 | 0.2 | failed | 1 |
| 91 | [ST028_003](../../Results_Thinking.md#L365) | 0.9 | 0.2 | failed | 1 |
| 92 | [ST029_001](../../Results_Thinking.md#L368) | 0.6 | 0.2 | failed | 1 |
| 93 | [ST029_002](../../Results_Thinking.md#L371) | 0.2 | 0.2 | failed | 1 |
| 94 | [ST030_001](../../Results_Thinking.md#L374) | 0.6 | 0.2 | failed | 1 |
| 95 | [ST030_002](../../Results_Thinking.md#L377) | 0.6 | 0.2 | failed | 1 |
| 96 | [ST030_003](../../Results_Thinking.md#L380) | 0.2 | 0.2 | failed | 1 |
| 97 | [ST031_001](../../Results_Thinking.md#L383) | 0.8 | 0.2 | failed | 1 |
| 98 | [ST031_002](../../Results_Thinking.md#L386) | 0.9 | 0.2 | failed | 1 |
