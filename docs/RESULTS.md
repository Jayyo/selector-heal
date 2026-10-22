# Results

One run, 22 October 2026. Pages collected that day (`data/urls/`), 12 synthetic
variants per page with up to 14 sampled steps each (fewer on pages with fewer
eligible controls), top-40 retrieval, the recorder and mutation engine in this
repository, Qwen2.5-3B-Instruct QLoRA (2 epochs, one seed). Thresholds were
chosen on validation sites and frozen in `docs/METHOD.md` before any test row
was scored. `selector-heal report` writes `results/summary.json` and prints
the method tables into `results/tables.md`; the method tables below are those,
with shorter labels and the thresholds written in, and the other tables are
read from `summary.json`.

All rates come with their counts. Intervals are 95% percentile intervals from
2,000 bootstrap resamples of site groups, not rows: rows from one site share a
template and their errors are correlated.

## The rows

| | pages | site groups | broken steps (rows) | successor present (S) | removed (D) |
|---|---|---|---|---|---|
| train (look-alike deletions) | 89 | 79 | 3,041 | 1,886 | 1,155 |
| validation, look-alike | 16 | 14 | 568 | 351 | 217 |
| validation, uniform | 16 | 14 | 551 | 356 | 195 |
| test, look-alike | 55 | 55 | 2,018 | 1,356 | 662 |
| test, uniform | 55 | 55 | 2,026 | 1,346 | 680 |

126 main-list URLs gave 107 snapshots and 75 test-list URLs gave 69 (bot walls,
dead hosts, pages without laid-out controls). 16 snapshots were excluded from
all splits: 11 test-list pages on a site that is also in the main list, and 5
pages whose final URL had already been collected.

Most sampled steps survive the change: on the look-alike test pages, 6,935
step-variants kept a working locator (`INTACT`) and 2 resolved to an equivalent
control, 2,018 broke and became rows, and 201 were excluded (139 no longer laid
out, 43 deleted with an equivalent control left behind, 19 cloned by the HTML
parser). Every build passed gates A-E and L, including a byte-identical
rebuild of three pages.

On the look-alike test rows, the stored locator was a role+name locator for
1,152 rows, a CSS path for 792, an id for 43, text for 30 and a form name for
1. It matched nothing in 1,814 rows, several elements in 112, and a single
wrong element in 92. Retrieval found an acceptable element for 1,354 of the
1,356 successor rows, and it was already the top candidate for 1,210 of them
(89.4%).

## Main result: test, look-alike deletions

2,018 rows from 55 site groups. S = 1,356, D = 662.

| method | answered | correct / S | wrong picks | false repairs / D | wrong per attempt | successor recall | deletion detection |
|---|---|---|---|---|---|---|---|
| Top retrieved candidate, always answers | 2018/2018 (1.00) | 1210/1356 | 146 | 662/662 | 0.400 [0.364, 0.439] | 0.892 [0.863, 0.921] | 0.000 |
| Top candidate if retrieval score >= 13.76 | 406/2018 (0.20) | 380/1356 | 7 | 19/662 | 0.064 [0.036, 0.098] | 0.280 [0.239, 0.324] | 0.971 [0.952, 0.987] |
| Fallback chain over recorded locators | 759/2018 (0.38) | 629/1356 | 22 | 108/662 | 0.171 [0.116, 0.233] | 0.464 [0.389, 0.545] | 0.837 [0.780, 0.890] |
| Learned gate (p >= 0.572) + top candidate | 1100/2018 (0.55) | 1055/1356 | 4 | 41/662 | 0.041 [0.026, 0.058] | 0.778 [0.721, 0.832] | 0.938 [0.914, 0.959] |
| Qwen2.5-3B-Instruct, not fine-tuned | 1241/2018 (0.61) | 532/1356 | 411 | 298/662 | 0.571 [0.539, 0.600] | 0.392 [0.358, 0.428] | 0.550 [0.503, 0.597] |
| Fine-tuned, own abstain point (margin >= 0) | 1258/2018 (0.62) | 1161/1356 | 53 | 44/662 | 0.077 [0.053, 0.104] | 0.856 [0.818, 0.890] | 0.934 [0.906, 0.959] |
| Fine-tuned, margin >= 2.01 | 1101/2018 (0.55) | 1054/1356 | 24 | 23/662 | 0.043 [0.028, 0.060] | 0.777 [0.731, 0.822] | 0.965 [0.945, 0.982] |
| Fine-tuned abstain decision + top candidate | 1258/2018 (0.62) | 1112/1356 | 102 | 44/662 | 0.116 [0.090, 0.143] | 0.820 [0.782, 0.855] | 0.934 [0.906, 0.959] |

"Answered" is coverage. "Wrong per attempt" counts wrong picks and false
repairs over everything the method answered. "Deletion detection" is the share
of removed-control rows on which the method abstained (A / D in
`docs/METHOD.md`).

### Uniform deletions

2,026 rows from the same 55 site groups, same edits, deletions picked at random.
S = 1,346, D = 680.

| method | answered | correct / S | wrong picks | false repairs / D | wrong per attempt | successor recall | deletion detection |
|---|---|---|---|---|---|---|---|
| Top retrieved candidate, always answers | 2026/2026 (1.00) | 1201/1346 | 145 | 680/680 | 0.407 [0.369, 0.444] | 0.892 [0.862, 0.922] | 0.000 |
| Top candidate if retrieval score >= 13.76 | 395/2026 (0.19) | 370/1346 | 11 | 14/680 | 0.063 [0.033, 0.095] | 0.275 [0.232, 0.319] | 0.979 [0.967, 0.990] |
| Fallback chain over recorded locators | 730/2026 (0.36) | 625/1346 | 25 | 80/680 | 0.144 [0.090, 0.202] | 0.464 [0.392, 0.541] | 0.882 [0.831, 0.926] |
| Learned gate (p >= 0.572) + top candidate | 1084/2026 (0.54) | 1052/1346 | 6 | 26/680 | 0.030 [0.017, 0.043] | 0.782 [0.727, 0.834] | 0.962 [0.945, 0.978] |
| Qwen2.5-3B-Instruct, not fine-tuned | 1234/2026 (0.61) | 532/1346 | 413 | 289/680 | 0.569 [0.533, 0.603] | 0.395 [0.359, 0.433] | 0.575 [0.535, 0.617] |
| Fine-tuned, own abstain point (margin >= 0) | 1264/2026 (0.62) | 1161/1346 | 46 | 57/680 | 0.081 [0.059, 0.105] | 0.863 [0.828, 0.895] | 0.916 [0.891, 0.942] |
| Fine-tuned, margin >= 2.01 | 1107/2026 (0.55) | 1049/1346 | 28 | 30/680 | 0.052 [0.035, 0.072] | 0.779 [0.735, 0.823] | 0.956 [0.938, 0.973] |
| Fine-tuned abstain decision + top candidate | 1264/2026 (0.62) | 1110/1346 | 97 | 57/680 | 0.122 [0.090, 0.154] | 0.825 [0.784, 0.863] | 0.916 [0.891, 0.942] |

The look-alike condition was meant as the harder one; on test the two
conditions give a similar picture. The learned gate makes more false repairs
under look-alike deletions (41 vs 26).

## Reading the table

**Recall alone puts the always-answer baseline first.** Always answering with
the top retrieved candidate has the highest successor recall (0.892), because
the correct element is the top candidate for 89% of the successor rows. It is
also wrong on 40% of its answers, because it answers every step whose control
was removed. Any recall number on this benchmark has to be read next to
coverage and wrong answers per attempt.

**Pre-specified paired comparisons.** The protocol asked for paired
differences of the fine-tuned model at its own abstain point against the top
candidate and against the learned gate, computed on the same site-group
resamples:

| fine-tuned (margin >= 0) minus | condition | successor recall | wrong per attempt | deletion detection | coverage |
|---|---|---|---|---|---|
| top candidate | look-alike | [-0.066, -0.007] | [-0.366, -0.283] | [+0.906, +0.959] | [-0.415, -0.341] |
| top candidate | uniform | [-0.057, -0.001] | [-0.363, -0.290] | [+0.891, +0.942] | [-0.413, -0.342] |
| learned gate | look-alike | [+0.046, +0.109] | [+0.015, +0.061] | [-0.027, +0.018] | [+0.050, +0.108] |
| learned gate | uniform | [+0.045, +0.113] | [+0.034, +0.070] | [-0.069, -0.022] | [+0.060, +0.117] |

Against the top candidate, the model gives up a little recall (it abstains on
some steps the top candidate gets right) for a much lower error rate. Against
the learned gate, it answers more, recovers more and is wrong more often;
under uniform deletions it also abstains on fewer removed controls.

**Added after the test results: both at their validation-chosen thresholds.**
Paired differences, fine-tuned at margin >= 2.01 minus learned gate:

| condition | successor recall | wrong per attempt | deletion detection | coverage |
|---|---|---|---|---|
| look-alike | [-0.028, +0.024] | [-0.016, +0.019] | [+0.008, +0.048] | [-0.020, +0.018] |
| uniform | [-0.036, +0.031] | [+0.009, +0.036] | [-0.024, +0.014] | [-0.012, +0.035] |

Under look-alike deletions the observed numbers are nearly identical (recall
0.777 vs 0.778, wrong per attempt 0.043 vs 0.041, both answering 55%), and the
intervals for recall, error rate and coverage include zero. That does not
show the two are equivalent; it shows this run cannot tell them apart. The
model abstained on more removed controls (0.965 vs 0.938). Under uniform
deletions the model was wrong on 5.2% of its answers and the gate on 3.0%.
The two also err differently: the gate's errors are almost all false repairs
(4 wrong picks, 41 false repairs), the model's are split (24 and 23).

**Exploratory: matched coverage.** Not pre-specified. Each method's threshold
is swept over the look-alike test rows and read at the first threshold whose
coverage reaches the target. Achieved coverage is within 0.001 of the target
except where noted.

| target coverage | retrieval score | learned gate | fine-tuned margin |
|---|---|---|---|
| 0.30 | 0.130 wrong, recall 0.415 (at 0.32) | 0.000, 0.447 | 0.008, 0.443 |
| 0.40 | 0.129, 0.519 | 0.000, 0.596 | 0.012, 0.588 |
| 0.50 | 0.172, 0.617 | 0.018, 0.731 | 0.030, 0.722 |
| 0.55 | 0.203, 0.653 | 0.044, 0.782 | 0.044, 0.782 |
| 0.60 | 0.230, 0.687 | 0.083, 0.819 | 0.066, 0.834 |
| 0.70 | 0.272, 0.759 | 0.178, 0.857 | 0.137, 0.900 |
| 0.80 | 0.320, 0.810 | 0.264, 0.876 | 0.230, 0.917 |

At 50% coverage and below the gate has the lower error rate, at 55% the two
are equal, and from 60% up the fine-tuned model is wrong less often and
recovers more. Under uniform deletions the crossing comes later: at 55% the
gate is at 0.038 and the model at 0.053, at 60% they are at 0.084 and 0.073.
These are point estimates on the test rows, without intervals.

**Part of what the model adds is the choice of element.** With exactly the
same answer/abstain decisions, answering with the model's own pick instead of
the top retrieved candidate cuts wrong picks from 102 to 53 (look-alike) and
from 97 to 46 (uniform).

**Fine-tuning moves the language model from 57% to 8% wrong per attempt.**
With the same prompt and no fine-tuning, the base model is wrong on 0.571
[0.539, 0.600] of its answers, recovers 0.392 of successors and abstains on
0.550 of removed controls. Fine-tuned, at its own abstain point: 0.077, 0.856
and 0.934.

**Thresholds did not keep their promise.** All three thresholds met the 2%
budget on the look-alike validation rows they were chosen on. On test they
gave 3.0% to 6.4% wrong answers per attempt, 1.5 to 3.2 times the budget:

| threshold | validation, look-alike (chosen here) | validation, uniform | test, look-alike | test, uniform |
|---|---|---|---|---|
| retrieval score >= 13.76 | 0.015 | 0.008 | 0.064 | 0.063 |
| learned gate p >= 0.572 | 0.017 | 0.010 | 0.041 | 0.030 |
| fine-tuned margin >= 2.01 | 0.020 | 0.023 | 0.043 | 0.052 |

The model threshold was already above budget on the uniform validation rows.
With 14 validation site groups, none of the three operating points carried
over to unseen sites in this run; a threshold chosen this way is a setting,
not a guarantee.

## Order robustness

The fine-tuned model scored the look-alike test rows under four seeded
candidate orders, each read at the model's own abstain point (margin >= 0).
No prompt was shortened under any order, so the differences come from the
order alone. A row is counted below when at least two of the four orders
disagree.

| | rows | answer/abstain decision differs across orders | answered element differs | outcome differs |
|---|---|---|---|---|
| look-alike test | 2,018 | 196 (9.7%) | 242 (12.0%) | 250 (12.4%) |

Aggregate numbers barely move (successor recall 0.853-0.858, wrong per attempt
0.076-0.083 across the four orders), but one row in eight gets a different
outcome under at least one of the four orders.

## Breakdowns (look-alike test)

By the strategy of the stored locator, outcomes as correct / wrong picks /
false repairs:

| stored strategy | rows (S / D) | top candidate | learned gate | fine-tuned, own abstain |
|---|---|---|---|---|
| role + name | 1,152 (697 / 455) | 632 / 65 / 455 | 561 / 0 / 9 | 616 / 16 / 15 |
| CSS path | 792 (607 / 185) | 532 / 75 / 185 | 455 / 4 / 32 | 504 / 35 / 29 |
| id | 43 (29 / 14) | 25 / 4 / 14 | 21 / 0 / 0 | 23 / 1 / 0 |
| text | 30 (23 / 7) | 21 / 2 / 7 | 18 / 0 / 0 | 18 / 1 / 0 |

The one row stored as a form name (a removed control, on which the gate and
the model abstained) is left out of the table.

35 of the model's 53 wrong picks come from steps stored as a CSS path, which
break on structural edits; the top candidate's wrong picks concentrate there
too (75 of 146). On the 329 successor rows where the element's own label was
rewritten, the model recovered 282 with 6 wrong picks (gate: 251 and 0; top
candidate: 287 and 42).

The fine-tuned model's 97 wrong answers are spread over 36 of the 55 test
sites; the three sites with the most (quasar.dev 11, arco.design 10,
python.org 7) account for 28.

## Checks on the labels and the generator

- **Manual audit.** I read 12 validation rows with more than one gold element
  and 12 validation deletions. 23 of the 24 were consistent with the stated
  policy (duplicated header/footer links, cloned carousel slides, a menu whose
  items all open one page, in-page anchors). One deletion was ambiguous from
  static HTML: a "전체메뉴 열기" (open full menu) button removed while a
  "전체메뉴" button remained.
- **Generator artifacts.** Among retrievable successor rows, the gold element
  was the only candidate with a generator-shaped id or test id in 5 of 1,878
  train rows (0.3%) and 0 of 348 validation rows, and the only candidate whose
  label came from the relabel list in 5 train rows (0.3%) and 2 validation rows
  (0.6%).
- **Shared templates.** One cross-split page pair had a control-label overlap
  above 0.3: Element Plus's button page (train) and Naive UI's button page
  (test), Jaccard 0.31.

## Side measurement: identifiers on public consoles

`selector-heal survey` on `data/urls/consoles.txt`, the same day. 30 URLs were
listed; 12 rendered at least 20 button-like controls (the rest no longer
resolve, land on a login page, or render too little). Links with a real href
are counted separately and left out.

| console | button-like controls | with test id, stable-looking id or name | recorder stored a CSS path |
|---|---|---|---|
| Grafana play dashboard | 20 | 70% | 1 of 20 |
| Redmine issues | 60 | 50% | 52 of 60 |
| OpenProject work packages | 347 | 5% | 100 of 150 sampled |
| GitLab explore | 74 | 41% | 29 of 74 |
| Wikidata recent changes | 41 | 27% | 12 of 41 |
| Apache Jira projects | 39 | 8% | 31 of 39 |
| refine.dev example admin | 29 | 0% | 27 of 29 |
| AdminLTE demo | 168 | 35% | 41 of 150 sampled |
| CoreUI demo | 35 | 9% | 18 of 35 |
| Tabler demo | 87 | 5% | 46 of 87 |
| GitLab issues | 129 | 90% | 61 of 129 |
| VS Code issues on GitHub | 89 | 39% | 24 of 89 |

Median 31%, pooled 29% (1,118 controls) carry a durable-looking identifier. Of
903 controls the recorder ran on, it stored a CSS path for 442 (49%), role +
name for 219 (24%), an id for 133, text for 64, a test id for 43 and a form
name for 2. Having a test id is not the same as having a unique one: on
GitLab's issue list 90% of buttons carry one, but the same test id repeats on
every row, so the recorder falls back to a structural path for almost half of
them. This is a convenience sample of consoles reachable without an account,
including three admin templates, not a sample of business software.

## Deviations from the protocol

- Added after the test results: the paired comparison at the
  validation-chosen thresholds (fine-tuned margin >= 2.01 vs learned gate),
  the exploratory matched-coverage table, and the layout of the figure. The
  pre-specified paired comparisons are reported as planned. No data, model,
  threshold or metric definition changed after the protocol commit.

## What these numbers do not say

- They describe synthetic changes to public landing, documentation and portal
  pages, not real releases of the applications people record guides on (most
  of those are behind a login). The change mix is weighted by how test authors
  changed their selectors in ReproBreak, which is not the same as how pages
  change.
- Removed controls are a third of the test rows (662 of 2,018 look-alike, 680
  of 2,026 uniform) although each variant deletes only 3-15% of the controls:
  a removed control always breaks its step, while most edits leave the stored
  locator working. Wrong per attempt depends on that mix; with fewer
  deletions every method that answers removed steps would look safer.
- One trained model, one seed. Training variance is not measured.
- The test sites were never used for training or thresholds here, but some
  share component libraries with training sites; groups separate sites, not
  design systems.
- The candidate list is the output of one hand-written retriever. A different
  retriever would change both the difficulty and the baselines.
- Equivalence is policy-defined from static HTML. Two links to one URL count
  as the same action even if a script would treat them differently.
- "Wrong per attempt" counts a false repair and a wrong pick the same; which
  one costs more depends on what the step does.
