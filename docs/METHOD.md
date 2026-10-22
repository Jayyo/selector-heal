# Method

## The task

A recorded step (from a test, a guide, an RPA script) stores a locator for the
element the user acted on. After the site changes, the stored locator may stop
finding that element. Given the recorded element and the elements on the
changed page, a repair method must either:

- **answer** with the element that does the same thing now, or
- **abstain** ("none of these").

Answering with the wrong element is the expensive mistake: the step then acts
on a different control without any error. Abstaining when the element is still
there is a missed repair, which is cheaper.

Rows in this benchmark are only steps whose stored locator actually fails on
the changed page. Each row says whether an acceptable successor exists on the
page (`successor: PRESENT`) or the control was removed (`ABSENT`).

## Pipeline

```
collect     live page -> stamped DOM snapshot + its CSS, fonts and images
build       snapshot -> record locators -> mutate -> re-render -> broken steps -> rows
split       pages -> train / val / test by site group
fit-gate    learned-gate baseline on train rows
train       QLoRA fine-tune on train rows
infer       per-row decision logits for val and test
report      thresholds on val, everything else on test
```

### Snapshots

`collect` renders each URL in headless Chromium with JavaScript on, waits 3.5 s
after `DOMContentLoaded`, stamps every element with a `data-sh-track` id and
saves the serialised DOM. Stylesheets, fonts and images the page loaded are
saved with it. Pages with fewer than five laid-out controls are dropped (bot
walls, error pages, pages that render nothing without interaction). Failed URLs
were retried once with an 8 s wait.

### Rendering

Everything the build reads comes from one renderer: JavaScript off (the
snapshot is already the post-hydration DOM; a framework booting again would
replace the nodes and lose the tracking ids), the document served at its
original URL, subresources served from the snapshot with permissive CORS
headers and anything else aborted, service workers blocked, a fixed 1440x900
viewport, locale and time zone, and the document animation timeline paused.
The unchanged page and every mutated variant go through the same HTML
serialiser before rendering. This makes builds repeatable offline; it does not
make the render identical to what a browser showed at collection time (missing
resources, scripts that would have run, a different font fallback).

### The recorder

For each sampled step, the recorder tries these strategies in order and stores
the first locator that matches exactly the target element on the record-time
render:

| order | strategy | stored as |
|---|---|---|
| 1 | test id (`data-testid`, `data-test-id`, `data-test`, `data-cy`, `data-qa`) | `[data-testid="..."]` |
| 2 | id, if it does not look generated | `#id` |
| 3 | `name` on form controls | `input[name="..."]` |
| 4 | interactive ARIA role + accessible name | `getByRole(role, { name, exact: true })` |
| 5 | visible text | `getByText(text, { exact: true })` |
| 6 | structural path | `#stable-ancestor > div:nth-of-type(2) > button:nth-of-type(1)` |

Resolution always goes through Playwright, so accessible names, text matching
and visibility follow Playwright's locator semantics: role locators skip hidden
elements, text and CSS locators do not. An id "looks generated" if it is longer
than 64 characters, contains a run of three or more digits, contains a colon,
is a hex blob or UUID, starts with a digit, or starts with a common framework
prefix (`mui-`, `react-`, `radix-`, `ember`...). This is a heuristic, not a
test of stability. The structural path anchors at the nearest ancestor with
such a stable, unique id, else at `body`.

The order is a design choice: explicit automation contracts first, then
attributes developers usually keep, then what the user sees, then structure.
Other recorders order these differently, and the order changes which steps
break under which change.

Every strategy that could be computed is stored as the step's fingerprint, with
a flag for whether it uniquely matched the target when recorded.

Each stored locator is replayed on a second, unchanged render; steps whose
locator does not find the same element again are excluded (gate A).

### Mutations

A variant samples one *bundle* of change axes from the bundle frequencies in
`data/distributions/reprobreak_changes.json`. Those come from classifying the
9,572 old/new locator pairs in ReproBreak (`reprobreak.py`: regexes over CSS
selectors plus the `getByTestId` / `getByRole` / `getByText` / `cy.contains`
APIs). 7,098 pairs map to at least one axis this engine can apply; the rest are
dropped. A locator pair records how a test author's selector changed, which is
not the same as how the page changed, so the result is a projection used to
weight synthetic edits, not a measurement of UI change. The DOM edits
themselves are this repo's approximations:

| axis | edit |
|---|---|
| CLASS_CHURN | replace CSS-in-JS / utility classes and about half of the rest with hashed names |
| ID_CHANGE / ID_REMOVED / ID_ADDED | keep the id prefix with a new suffix / drop the id / add a generated id |
| ATTR_CHANGE | restyle `name` (snake/camel/kebab), reword `title`, `placeholder`, `aria-label`, sometimes drop `aria-label` |
| TESTID_CHANGE | drop or suffix the test id |
| TAG_SWAP | `button` <-> `a[role=button]`, `input[type=submit]` -> `button` |
| DEPTH_CHANGE | wrap in a new `div`, or unwrap a single-child `div` |
| POSITION_CHANGE | move the element after a random sibling |
| COPY_CHANGE | reword the element's own label: full relabel, change one word, extend or shorten, or change case |

The bundle is applied to a random 25-80% of the page's elements, with at least
the same share of the actionable controls. The copy-change sub-types follow the
classifier's definitions and are weighted by the shares among ReproBreak pairs
with one label on each side (n = 844): replace 51% (no shared word), substantial
25% (a shared word, neither label contains the other), partial 20% (one label
contains the other; the engine extends or shortens), case 4%. Full relabels draw
from a fixed list of common UI labels in English or Korean, and extensions from
a short suffix list; many of these also occur naturally on pages, but a model
could still learn to associate some of them with relabelled controls.

**Deletion.** Each variant also removes 3-15% of the labelled controls that were
laid out on the unchanged render, as whole behavioural groups that follow the
equivalence policy below (all links to one destination; other controls by label
plus id, test id or form action), so a removed step has no surviving equivalent.
The budget counts controls; a group that would overshoot it is skipped. Two
conditions:

- `uniform`: groups are picked at random.
- `lookalike`: while groups that have a similar control nearby remain (same tag,
  character-bigram Jaccard >= 0.3 on the label, within 100 positions in document
  order), each pick comes from them with probability 0.7, and the similar
  control's group is then protected from deletion. This is a stress condition,
  not an estimate of how often real releases remove controls.

Edits and deletions use separate random streams, so the two conditions apply
identical edits and differ only in what is deleted.

### Labels and the ledger

Each sampled step on each variant ends up in exactly one ledger status:

| status | meaning | in rows? |
|---|---|---|
| `UNRECORDABLE` | no strategy uniquely matched the element | no |
| `UNSTABLE_CONTROL` | the stored locator missed on the unchanged re-render | no |
| `RENDER_FAIL` | the mutated variant failed to render | no |
| `DUPLICATED_ID` | the parser cloned the element after the change; lineage is ambiguous | no |
| `OBSERVATION_FAILURE` | the element is in the DOM but no longer laid out | no |
| `IDENTITY_SUSPECT` | the tracked element shares nothing with the original and no edit explains it | no |
| `EQUIV_EXCLUDED` | the element was deleted but an equivalent control survives | no |
| `RESOLVE_ERROR` | Playwright could not evaluate the stored locator on the changed page | no |
| `INTACT` | the stored locator still finds the element | no (control) |
| `INTACT_EQUIVALENT` | it finds exactly one equivalent element | no (control) |
| `HEALING` | the stored locator fails: matches nothing, several elements, or one wrong element | yes |

Elements the parser already cloned on the unchanged render are not sampled.
Gate L checks that every sampled step has exactly one status and that the
`HEALING` entries are exactly the rows.

For a `HEALING` row with a surviving element, the gold set is the element plus
every candidate that is equivalent to it: a link to the same resolved
destination, whatever its label (a product card's image, title and "View" link
all go to one page), or another control with the same label (case-insensitive)
and the same id or test id with its attribute. Destinations and identifiers are
compared exactly; "#" and `javascript:` links have no destination. This is
policy-defined equivalence: static HTML cannot prove that two controls do the
same thing, and two "Delete" buttons with nothing else in common are never
merged. For a deleted element the gold set is empty.

"Label" here is the `name` field: an approximation of the accessible name
(aria-label, aria-labelledby, associated `<label>`s, title, placeholder, the
value of button-type inputs, the text, an image's alt). Sampled steps must have
a non-empty name or text, be laid out, enabled and not `type=hidden`.

### Retrieval

All laid-out interactive elements on the changed page are scored against the
recorded element by `retrieval.candidate_score` (tag/role compatibility, label
and text token overlap, matching test id / name / id / href, box position and
size, section heading, ancestor overlap) and the top 40 are the candidates.
`retrieval: HIT` means a gold element is among them. Retrieval misses stay in
the successor denominator as missed repairs.

## Methods compared

All methods are scored on the same rows.

| method | answers with | abstains when |
|---|---|---|
| Top candidate | the highest-scoring candidate | only when nothing was retrieved |
| Retrieval threshold | the highest-scoring candidate | its score is below a threshold |
| Fallback chain | the first other recorded locator (recorder order, only those unique at record time) that now matches exactly one element anywhere on the page | none does |
| Learned gate | the highest-scoring candidate | a logistic regression on 12 retrieval features says it is probably wrong |
| Base model | its top label | it prefers "-" (no fine-tuning) |
| Fine-tuned model | its top label | it prefers "-", or its margin is below a threshold |
| Fine-tuned gate + top candidate | the highest-scoring candidate | the fine-tuned model prefers "-" |

The learned gate is the cheap control for the model: its 12 features are the
top score, the gap to the second score, the candidate count, label equality and
token overlap with the recorded element, matching tag, destination, id and test
id, box shift, matching section heading, and how many candidates share the top
label. Its target is "the top candidate is acceptable". The last row separates
the model's answer/abstain decision from its choice of element. The fallback
chain is the only method not limited to the retrieved candidates, so it can be
right on a retrieval miss.

## Model

`Qwen2.5-3B-Instruct` (revision `aa8e725`), 4-bit NF4 with double quantisation,
LoRA rank 16, alpha 32, dropout 0.05 on all attention and MLP projections.

The prompt shows the recorded element and up to 40 candidates, one per line,
each with a single-character label (`A`-`Z`, `a`-`n`):

```
Recorded element:
tag=button name=Save changes id=save box=(812,640,120,36)

Elements on the changed page:
[A] tag=a name=Docs href=docs box=(40,12,44,20)
[B] tag=button name=Apply role=button box=(812,652,96,36)
...
Answer:
```

The decision reads the next-token logits after the chat template's answer
prefix, restricted to the shown labels plus `-`. Training minimises
`logsumexp(valid labels) - logsumexp(acceptable labels)`, the negative log of
the total probability of all acceptable answers; rows without an acceptable
candidate (removed control, or retrieval miss) target `-`. Candidate order is
reshuffled every epoch. A prompt over 4,096 tokens loses candidates from the end
of its shown order, without looking at labels; a training row whose gold
candidates were all cut is skipped. The gate margin is
`max(candidate logits) - logit("-")`; the model answers exactly when it is >= 0.
Label logits are computed in fp32 from the final hidden state, the base model is
prepared the same way for training and scoring (norms and embeddings in fp32),
and scoring runs one row at a time.

## Splits

Pages are grouped by site with a small suffix heuristic, not a full public
suffix list: the last two host labels, or three for Korean second-level
suffixes such as `go.kr` and hosting suffixes such as `github.io`, plus manual
merges listed in `configs/split.json`.

- **test**: pages from `data/urls/test.txt` whose site group never appears in
  `data/urls/main.txt`, neither as a listed URL (collected or not) nor as the
  final URL of a collected page. Test-list pages on a main-list site are
  excluded, not moved.
- **val**: main-list groups named in the config (six sites used for development
  in the earlier version of this harness; a seventh, PrimeFaces, now refuses
  headless requests), plus main-list groups whose SHA-1 starts with `0` or `1`.
- **train**: every other main-list group.

The split tool also flags page pairs across splits whose control labels overlap
heavily (Jaccard >= 0.3), as a rough signal of shared templates. Groups
separate sites, not design systems: a component library in training and a
different site built on the same library in test are not separated.

Provenance of the test lists: both were written in August 2026 for a held-out
evaluation of the earlier version. No model was trained, scored or tuned on
them there; descriptive page statistics and one simple-rule baseline number
were computed on `test-a`. Several of their hosts were later added to the
earlier version's training corpus; those hosts are in `main.txt` and are
therefore excluded from test here.

## Evaluation protocol

Fixed before any test-split number was computed; the commit that adds this
section precedes the commit that adds test results.

1. Builds: 12 variants per page, 14 sampled steps per variant, top 40
   candidates, seed 0. Look-alike deletion for train, val and test; uniform
   deletion for val and test.
2. The learned gate is fit on train rows of the look-alike build.
3. One fine-tuning run (configuration below). No model selection on test.
4. Thresholds are chosen on validation rows of the look-alike build with one
   rule: the lowest threshold whose wrong answers per attempt is at most 2%,
   among thresholds with at least 30 attempts. If none qualifies, that
   operating point is reported as missing. The same thresholds are applied to
   both test conditions.
5. Primary results are on test: outcome counts and rates for every method, per
   deletion condition, with 95% intervals from 2,000 site-group bootstrap
   resamples, and paired differences fine-tuned minus top candidate and minus
   learned gate.
6. Order robustness: the fine-tuned model is scored on the look-alike test rows
   under four candidate orders (shuffle seeds 1-4); seed 1 is the official
   order. Reported: how often the answer/abstain decision, the chosen element,
   and the outcome change across orders, separately for rows whose prompt was
   shortened under some order.
7. Anything changed after this protocol was committed is listed under
   "Deviations" in RESULTS.md.

Thresholds chosen this way on the 568 validation rows of the look-alike build
(14 site groups), before any test row was scored: retrieval score >= 13.76,
learned-gate probability >= 0.572, fine-tuned gate margin >= 2.01. The learned
gate was fit on 3,041 train rows and the model trained on the same rows.

Definitions, with S successor rows and D deleted rows:

```
S = C + M + W        correct, missed (abstained), wrong pick
D = A + F            correct abstain, false repair
attempts = C + W + F
coverage = attempts / (S + D)
wrong answers per attempt = (W + F) / attempts
successor recall = C / S
deletion detection = A / D
```

## Fine-tuning configuration

| setting | value |
|---|---|
| base | Qwen/Qwen2.5-3B-Instruct @ aa8e72537993ba99e69dfaafa59ed015b17504d1 |
| training rows | train split, look-alike build, all `HEALING` rows |
| epochs | 2 |
| optimiser | AdamW, lr 1e-4, cosine decay, 3% warmup, grad clip 1.0 |
| batch | 1 row, 16-row gradient accumulation |
| max prompt | 4,096 tokens (no training prompt reached it) |
| seed | 1 |
| hardware | one RTX 3090 24 GB, about 2.3 hours |

## Reproducing

```bash
pip install -e ".[train,plot,dev]"
python -m playwright install chromium

selector-heal collect --urls data/urls/main.txt --out work/snapshots/main --concurrency 6
selector-heal collect --urls data/urls/test.txt --out work/snapshots/test --concurrency 6
selector-heal collect --urls data/urls/main.txt --out work/snapshots/main --retry-failed --wait-ms 8000
selector-heal collect --urls data/urls/test.txt --out work/snapshots/test --retry-failed --wait-ms 8000
selector-heal split --snapshots work/snapshots/main work/snapshots/test \
    --config configs/split.json --out work/splits.json

selector-heal build --snapshots work/snapshots/main --out work/data/main_lookalike --workers 8
selector-heal build --snapshots work/snapshots/main --out work/data/val_uniform --deletion uniform \
    --pages <comma-separated val page keys from work/splits.json> --workers 8
selector-heal build --snapshots work/snapshots/test --out work/data/test_lookalike --workers 8
selector-heal build --snapshots work/snapshots/test --out work/data/test_uniform --deletion uniform --workers 8
selector-heal check --data work/data/main_lookalike --manifest work/splits.json \
    --rebuild work/snapshots/main

selector-heal fit-gate --data work/data/main_lookalike --manifest work/splits.json --out work/gate.json
selector-heal train --data work/data/main_lookalike --manifest work/splits.json --out work/runs/r1
selector-heal infer --data work/data/main_lookalike --manifest work/splits.json --split val \
    --adapter work/runs/r1/adapter --out work/dumps/model_val_lookalike.jsonl
# one infer call per dump listed in configs/report.json (--order shuffle:2..4 for the
# order runs, no --adapter for the base-model rows), then:
selector-heal report --config configs/report.json --root work --out results
```

Sites change and some block headless browsers, so a re-collection yields a
different corpus. The snapshot hashes of the corpus behind the published
numbers are in `data/splits.json`; the fixture pipeline in `tests/` is the part
that reproduces exactly.
