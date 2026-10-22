import random

from bs4 import BeautifulSoup

from selector_heal.mutate import Mutator, normalize_html

HTML = """<!DOCTYPE html><html><head><title>t</title></head><body>
<nav><a data-sh-track="c1" href="/docs" class="nav css-1ab">Docs</a>
<a data-sh-track="c2" href="/blog">Blog</a></nav>
<form data-sh-track="c3"><input data-sh-track="c4" name="user_email" placeholder="Email">
<button data-sh-track="c5" id="save" class="btn">Save</button>
<button data-sh-track="c6">Cancel</button></form>
<footer data-sh-track="c7"><a data-sh-track="c8" href="/docs">Docs</a></footer>
<noscript><p>enable js</p></noscript>
</body></html>"""


def tracks(html):
    return sorted(el["data-sh-track"] for el in BeautifulSoup(html, "html.parser").find_all(True)
                  if el.get("data-sh-track"))


def test_same_seed_same_output():
    a = Mutator(seed=7).mutate(HTML, random.Random(1), "lookalike")
    b = Mutator(seed=7).mutate(HTML, random.Random(1), "lookalike")
    assert a.html == b.html and a.deleted == b.deleted and a.per_element == b.per_element


def test_deletion_condition_does_not_change_the_edits():
    # with nothing deleted, both conditions must produce the same page
    a = Mutator(seed=3).mutate(HTML, random.Random(5), "uniform", delete_range=(0.0, 0.0))
    b = Mutator(seed=3).mutate(HTML, random.Random(5), "lookalike", delete_range=(0.0, 0.0))
    assert a.html == b.html


def test_lineage_kept_and_new_nodes_namespaced():
    res = Mutator(seed=11).mutate(HTML, random.Random(2), "uniform", delete_range=(0.0, 0.0))
    after = tracks(res.html)
    assert {"c1", "c2", "c4", "c5", "c6", "c8"} <= set(after)
    assert all(t.startswith(("c", "m11_")) for t in after)


def test_behavioural_groups_are_deleted_together():
    m = Mutator(seed=0)
    soup = BeautifulSoup(HTML, "html.parser")
    # same real destination: one group, so both Docs links go at once
    for seed in range(20):
        s = BeautifulSoup(HTML, "html.parser")
        gone, _ = m.delete_groups(s, random.Random(seed), ratio=0.2, condition="uniform")
        assert ("c1" in gone) == ("c8" in gone)
    assert m.behaviour_key(soup.find(attrs={"data-sh-track": "c6"})) is None


def test_normalize_drops_noscript_and_keeps_doctype():
    out = normalize_html(HTML)
    assert "<noscript" not in out and out.lower().startswith("<!doctype html>")


MANY = "<html><body>" + "".join(
    f'<a data-sh-track="h{i}" href="/">Home</a>' for i in range(10)) + "".join(
    f'<button data-sh-track="b{i}">Action {i}</button>' for i in range(10)) + "</body></html>"


def test_deletion_budget_counts_controls_not_groups():
    m = Mutator(seed=0)
    for seed in range(10):
        soup = BeautifulSoup(MANY, "html.parser")
        gone, _ = m.delete_groups(soup, random.Random(seed), ratio=0.2, condition="uniform")
        # budget is 4 controls; the ten identical Home links form one group of 10
        assert len(gone) <= 4 and not any(t.startswith("h") for t in gone)


def test_only_deletable_controls_are_removed():
    m = Mutator(seed=0)
    soup = BeautifulSoup(MANY, "html.parser")
    gone, _ = m.delete_groups(soup, random.Random(1), ratio=0.5, condition="uniform",
                              deletable={"b1", "b2", "b3"})
    assert set(gone) <= {"b1", "b2", "b3"}


def test_lookalike_keeps_the_look_alike():
    html = ("<html><body><button data-sh-track='a'>Save draft</button>"
            "<button data-sh-track='b'>Save drafts</button>"
            + "".join(f"<button data-sh-track='x{i}'>Unrelated {i * 7919}</button>" for i in range(8))
            + "</body></html>")
    m = Mutator(seed=0)
    for seed in range(20):
        soup = BeautifulSoup(html, "html.parser")
        gone, _ = m.delete_groups(soup, random.Random(seed), ratio=0.3, condition="lookalike")
        assert not ({"a", "b"} <= set(gone))


def test_case_edit_changes_the_label():
    m = Mutator(seed=0)
    m.copy_kinds, m.copy_weights = ["case"], [1.0]
    soup = BeautifulSoup("<button data-sh-track='c1'>Save changes</button>", "html.parser")
    btn = soup.button
    assert m._copy_change(btn) and btn.get_text() == "SAVE CHANGES"


def test_links_to_one_destination_are_one_group():
    html = ("<html><body><a data-sh-track='i' href='/p/1'><img alt=''></a>"
            "<a data-sh-track='t' href='/p/1'>Blue mug</a>"
            "<a data-sh-track='v' href='https://shop.test/p/1'>View</a></body></html>")
    soup = BeautifulSoup(html, "html.parser")
    keys = {Mutator.behaviour_key(a, "https://shop.test/") for a in soup.find_all("a")}
    assert keys == {("href", "https://shop.test/p/1")}
