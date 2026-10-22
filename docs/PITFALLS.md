# Pitfalls

Every item here happened at least once while building this harness, usually
silently: the pipeline ran, the numbers looked plausible, and they were wrong.
Each entry says what the symptom looked like and what the code does about it now.

## Measuring the wrong thing

**A constant policy matches the model.** If candidates are sorted by a similarity
score and the correct element is usually first, "always take the first
candidate" reaches the same successor recall as a fine-tuned model. A high
recall then says nothing about ranking. Every results table in this repo
includes that policy, and the gates print how often the correct element is
already the top candidate.

**Repairing steps that never broke.** Mutating a page and then sampling steps
independently of the mutation produces many steps whose stored locator still
works. Scoring those as repairs inflates recovery with re-finds. Only steps
whose stored locator fails on the changed page become rows (gate C); the rest
are counted as controls in the ledger.

**The recorder decides the population.** Which steps count as broken depends
on what the recorder stored: a role+name locator breaks on relabels, a CSS path
on wrapper inserts, an id on id churn. Changing the recorder changes the mix of
rows, so numbers from different recorders are not comparable. Rows record the
stored strategy and the break type so results can be sliced by them.

**Steps no user could have recorded.** Elements inside closed menus are in the
DOM with a zero-size box. Sampling them as targets creates steps a real
recording cannot produce, and they behave very differently (they are rarely
retrieved). Targets must be laid out, enabled and labelled; see
`build.eligible_target`.

**A model that answers less looks safer.** Wrong picks divided by all rows goes
down when a method abstains more. Results report wrong answers per attempt
next to coverage, and the risk-coverage curve shows the whole trade-off.

**Retrieval misses quietly leave the denominator.** If the correct element is
not among the retrieved candidates, no reranker can pick it, but the step is
still a missed repair. Those rows stay in the successor denominator.

## Re-rendering snapshots

**Hydration erases lineage.** Rendering a saved post-JS DOM with scripts on lets
the framework rebuild those nodes, dropping the tracking attributes. The
renderer keeps JavaScript off.

**Live subresources drift.** Re-rendering weeks later against live CSS changes
layout and visibility between the "before" and "after" pages. Stylesheets,
fonts and images are captured at collection time and served from the snapshot;
anything missing is aborted and counted.

**Animations run without scripts.** CSS animations and transitions still run,
so a carousel's boxes depend on when they are read. The renderer pauses the
document timeline before navigation. Without this, rebuilding the same pages
under load changed candidate geometry in a few rows per page.

**The HTML parser rewrites script-built DOMs.** A DOM built by scripts can hold
structures the parser never produces, such as an anchor inside an anchor.
Serialising and re-parsing it restructures the tree and clones some elements,
attributes included, so one tracking id can appear twice. Elements cloned
before any mutation are not eligible targets; a step whose element is cloned
after the change is excluded, and cloned elements leave the candidate pool.

**Pages that patch built-ins.** Some sites replace JavaScript built-ins in a way
that makes Playwright return `None` for any object or array from `evaluate`,
while strings still work. Every in-page script here returns `JSON.stringify(...)`.

**Before and after must share a serialiser.** The mutated page is serialised by
BeautifulSoup; the unchanged page has to go through the same round trip, or the
round trip itself shows up as a change. The control render (gate A) checks that
every stored locator still finds its element on an unchanged page.

## Labels

**One gold element when several are correct.** Pages repeat controls: the same
link in the header and the footer. Marking only the tracked one as correct
turns an equally valid pick into a "wrong pick". The gold set contains every
equivalent element. Two "Delete" buttons with nothing else in common are never
merged.

**Equivalence by label misses links that go to the same place.** Requiring the
same visible label marked a product card's title link as wrong when the
recorded step was its image link, and called a deleted "Explore Figma Design"
link removed while a "Figma Design" link to the same URL was still on the page.
On this corpus that affected about one row in seven. Links are now equivalent
when their resolved destinations match, and deletion groups follow the same
rule. Identifiers and URLs are compared exactly: lowercasing them merged
`/item/A` with `/item/a`.

**Truncation rewrites labels.** Dropping candidates to fit a context window can
drop the correct one; if the target then becomes "none of these", the model is
trained to give up on findable elements. Training skips such rows, and
inference drops candidates without looking at the label and records that it
did.

**Deleting one of two identical controls.** If an equivalent control survives,
the step is still doable; calling it a removal teaches abstention where a click
is right. Deletion removes whole behavioural groups, and a removed step with a
surviving equivalent is excluded.

**A deletion budget counted in the wrong unit.** Counting removed groups against
a budget measured in controls let one group of ten repeated links blow through
a four-control budget. The budget now counts controls and skips groups that
would overshoot.

## Training and inference

**Train and eval prompts drift apart.** Two prompt builders with small
differences (candidate formatting, truncation order, chat template) make the
evaluated model a different model. One builder is shared, and both sides read
the same logit position.

**Label tokens that are not single tokens.** The decision reads one logit per
label; if a label splits into two tokens after the chat template's answer
prefix, the reading is wrong. The encoder checks every label at that exact
boundary and refuses to run otherwise.

**Saturated confidence.** Softmax probability over the labels can sit near 1.0
for almost every row, so a probability threshold filters nothing. The gate
margin (best candidate logit minus the "none" logit) is what the thresholds use.

**Batching changes the answer.** With a 4-bit model in bf16, scoring the same
row alone or in a batch of four moved label logits by up to about one unit,
with no padding involved. Inference runs at batch size 1, like training, and
the decision logits are computed in fp32 from the final hidden state.

**Skipped rows inside an accumulation window.** Counting rows seen rather than
rows used made a skipped row delay the optimizer step, so one update averaged
31 rows' gradients divided by 16. Steps are now counted on rows actually used,
and a short window is rescaled.

**Abstention depends on candidate order.** Reordering the same candidates can
flip the answer/abstain decision even when the chosen element stays the same.
Evaluation shows candidates in a seeded per-row shuffle and reports how often
the decision flips across four orders.

## Bookkeeping

**Builds that cannot be repeated.** Python's `hash()` is salted per process, a
global RNG is shared by unrelated steps, and parallel workers write in
completion order. Seeds here come from blake2b over (page, variant, purpose),
each purpose has its own stream, and per-page shards are merged in sorted
order. Gate B rebuilds pages and compares bytes.

**Baselines and model on different rows.** Capping or sampling the model's
evaluation but not the baselines' compares two populations. Every method is
scored on exactly the same rows, and the report refuses a model dump whose row
ids or row contents differ from the rows being scored.

**Training sites in the test set.** A file built from training pages, scored as
if it were held out, looks much better than the real holdout. Splits are by
site group, the manifest is checked for groups in more than one split (gate
E), and training writes its site list next to the adapter.

**Thresholds tuned where they are reported.** A threshold picked on the same
rows it is evaluated on is an optimistic bound. Thresholds are picked on
validation sites with a fixed rule and applied unchanged to test sites. In an
earlier version of this harness, a threshold that met its error budget on six
sites missed it badly on a seventh, so the transfer is reported, not assumed.

**Row-level intervals.** Errors cluster by site, so treating rows as independent
gives intervals that are far too narrow. Intervals here resample site groups.
