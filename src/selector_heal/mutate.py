"""Synthetic UI changes, sampled as bundles of change axes.

Many old/new locator pairs in ReproBreak differ on more than one classified
axis (tag, classes and wrapper depth together, say), so a variant samples one
*bundle* of axes from that distribution and applies it to a random 25-80% of
the page. ReproBreak only informs which axes co-occur and how often; the DOM
edits themselves are this module's own approximations.

Tracking ids survive every edit, which defines lineage; the equivalence policy
and the render checks in build.py decide which steps become rows. Deletion
removes whole behavioural groups so a removed step has no surviving equivalent.
"""
from __future__ import annotations

import json
import random
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag

from .render import TRACK

AXES = (
    "CLASS_CHURN", "ID_CHANGE", "ID_REMOVED", "ID_ADDED", "ATTR_CHANGE",
    "TAG_SWAP", "DEPTH_CHANGE", "COPY_CHANGE", "POSITION_CHANGE", "TESTID_CHANGE",
)
DELETION_CONDITIONS = ("uniform", "lookalike")
ACTIONABLE = ("button", "a", "input", "select", "textarea")
SKIP = ("script", "style", "noscript", "template", "head", "meta", "link", "title")
HASHY = "abcdefghijklmnopqrstuvwxyz0123456789"
HANGUL = re.compile(r"[가-힣]")

DEFAULT_DIST = Path(__file__).resolve().parents[2] / "data" / "distributions" / "reprobreak_changes.json"

# Fallback when the distribution file has no copy-change breakdown.
DEFAULT_COPY_KINDS = {"replace": 0.5, "substantial": 0.25, "partial": 0.2, "case": 0.05}

# Plausible labels for a full relabel. Many of these also occur naturally on
# pages, which keeps "this label came from the generator" from being a giveaway.
RELABEL_EN = (
    "Continue", "Open", "View", "View details", "Details", "More", "Learn more", "Manage",
    "Options", "Settings", "Edit", "Update", "Apply", "Confirm", "Done", "Go", "Next", "Back",
    "Close", "Add", "Create", "New", "Remove", "Export", "Download", "Share", "Copy", "Submit",
    "Send", "Start", "Get started", "Sign in", "Log in", "Sign up", "Explore", "See all",
    "Show more", "Browse", "Search", "Filter", "Refresh", "Help", "Contact us", "Try it",
    "Preview", "Select", "Choose", "Save", "Cancel", "Overview", "Docs", "Pricing",
)
RELABEL_KO = (
    "더 보기", "자세히 보기", "바로가기", "확인", "적용", "완료", "열기", "닫기", "다음", "이전",
    "추가", "등록", "수정", "변경", "삭제", "검색", "조회", "신청", "제출", "저장", "취소",
    "내려받기", "공유", "복사", "설정", "관리", "선택", "전체 보기", "시작하기", "로그인",
    "회원가입", "문의하기", "안내", "목록", "상세보기",
)
SWAPS = (("확인", "제출"), ("저장", "적용"), ("삭제", "제거"), ("조회", "검색"),
         ("Confirm", "Submit"), ("Download", "Export"), ("Edit", "Modify"),
         ("Sign in", "Log in"), ("Delete", "Remove"), ("Learn more", "Read more"))
SUFFIX_EN = (" now", "...", " all", " options", " »")
SUFFIX_KO = ("하기", " 바로가기", "...", " 보기")
SUBST_EN = ("details", "settings", "options", "info", "list", "page", "items", "overview")
SUBST_KO = ("안내", "목록", "설정", "정보", "관리", "현황")


def load_distribution(path: Path | None = None):
    d = json.loads((path or DEFAULT_DIST).read_text(encoding="utf-8"))
    bundles, weights = [], []
    for name, count in d["bundles"].items():
        axes = tuple(a for a in name.split(" + ") if a in AXES)
        if axes:
            bundles.append(axes)
            weights.append(count)
    kinds = d.get("copy_change_kinds") or DEFAULT_COPY_KINDS
    return bundles, weights, kinds


def normalize_html(html: str) -> str:
    """Parse and re-serialise once, the same way every mutated variant is.

    Before and after pages must share a serialiser, or the parser round trip
    itself can look like a change. <noscript> is dropped because the snapshot was
    taken with scripts on, so its content was never on screen; meta refresh is
    dropped so an offline render cannot navigate away.
    """
    soup = BeautifulSoup(html, "html.parser")
    for el in soup.find_all("noscript"):
        el.decompose()
    for el in soup.find_all("meta"):
        if (el.get("http-equiv") or "").lower() == "refresh":
            el.decompose()
    return str(soup)


@dataclass
class MutationResult:
    html: str
    bundle: tuple = ()
    applied: list = field(default_factory=list)       # axes that changed at least one node
    per_element: dict = field(default_factory=dict)   # track -> axes that changed THAT node
    deleted: list = field(default_factory=list)       # tracks removed by deletion
    n_deleted_groups: int = 0


def _norm(t: str) -> str:
    return re.sub(r"\s+", " ", (t or "")).strip().lower()


def _ws(t: str) -> str:
    return re.sub(r"\s+", " ", (t or "")).strip()


def control_label(el: Tag) -> str:
    """aria-label, then visible text, then value/title -- close to what render.py calls name."""
    v = el.get("aria-label") or el.get_text(" ", strip=True)
    if not v and el.name == "input" and (el.get("type") or "").lower() in ("button", "submit", "reset"):
        v = el.get("value")
    return _ws(v or el.get("title") or "")


def _bigrams(s: str) -> set:
    s = _norm(unicodedata.normalize("NFKC", s or ""))
    return {s[i:i + 2] for i in range(len(s) - 1)} if len(s) >= 2 else ({s} if s else set())


class Mutator:
    def __init__(self, seed: int, dist_path: Path | None = None):
        self.seed = seed
        self.rng = random.Random(seed)
        self.bundles, self.weights, kinds = load_distribution(dist_path)
        self.copy_kinds = list(kinds.keys())
        self.copy_weights = [kinds[k] for k in self.copy_kinds]
        self._counter = 0
        self._per_element: dict = {}

    # -- bookkeeping ------------------------------------------------------
    def _new_track(self) -> str:
        self._counter += 1
        return f"m{self.seed}_{self._counter}"

    def _hash(self, n: int) -> str:
        return "".join(self.rng.choice(HASHY) for _ in range(n))

    def _note(self, el: Tag, axis: str):
        t = el.get(TRACK)
        if t:
            self._per_element.setdefault(t, set()).add(axis)

    # -- axes ---------------------------------------------------------------
    def _class_churn(self, el: Tag) -> bool:
        cls = el.get("class")
        if not cls:
            return False
        el["class"] = [f"css-{self._hash(6)}"
                       if re.match(r"^(css|sc|jsx|tw|emotion)-", c) or self.rng.random() < 0.5 else c
                       for c in cls]
        return True

    def _id_change(self, el: Tag) -> bool:
        if not el.get("id"):
            return False
        el["id"] = f"{el['id'].split('-')[0]}-{self._hash(5)}"
        return True

    def _id_removed(self, el: Tag) -> bool:
        if not el.get("id"):
            return False
        del el["id"]
        return True

    def _id_added(self, el: Tag) -> bool:
        if el.get("id"):
            return False
        el["id"] = f"el-{self._hash(6)}"
        return True

    def _restyle_name(self, v: str) -> str:
        if "_" in v:
            parts = v.split("_")
            return parts[0] + "".join(p[:1].upper() + p[1:] for p in parts[1:])
        if "-" in v:
            return v.replace("-", "_")
        if re.search(r"[a-z][A-Z]", v):
            return re.sub(r"([a-z])([A-Z])", r"\1_\2", v).lower()
        return v + self.rng.choice(("_input", "Field", "_value"))

    def _reword_attr(self, v: str) -> str:
        ko = bool(HANGUL.search(v))
        choice = self.rng.random()
        if choice < 0.4:
            return v + self.rng.choice((" 입력", " 선택") if ko else (" (optional)", " here", " now"))
        if choice < 0.7 and " " in v.strip():
            return " ".join(v.split()[:-1])
        return v.lower() if v != v.lower() else v.capitalize()

    def _attr_change(self, el: Tag) -> bool:
        touched = False
        if el.get("name") and self.rng.random() < 0.5:
            el["name"] = self._restyle_name(el["name"])
            touched = True
        for a in ("title", "placeholder", "aria-label"):
            if el.get(a) and self.rng.random() < 0.6:
                new = self._reword_attr(el[a])
                if new and new != el[a]:
                    el[a] = new
                    touched = True
        if el.get("aria-label") and self.rng.random() < 0.3:
            del el["aria-label"]
            touched = True
        return touched

    def _testid_change(self, el: Tag) -> bool:
        for a in ("data-testid", "data-test-id", "data-test", "data-cy", "data-qa"):
            if el.get(a):
                if self.rng.random() < 0.4:
                    del el[a]
                else:
                    el[a] = el[a] + "-" + self._hash(4)
                return True
        return False

    def _tag_swap(self, el: Tag) -> bool:
        """Design-system style swaps: button <-> a[role=button], input[type=submit] -> button."""
        if el.name == "button":
            el.name = "a"
            el["role"] = "button"
            el["href"] = "#"
            return True
        if el.name == "a" and (el.get("role") == "button" or not el.get("href")):
            el.name = "button"
            el.attrs.pop("href", None)
            el.attrs.pop("role", None)
            return True
        if el.name == "input" and el.get("type") in ("button", "submit"):
            label = el.get("value", "")
            el.name = "button"
            el.attrs.pop("value", None)
            if label and not el.get_text(strip=True):
                el.string = label
            return True
        return False

    def _depth_change(self, el: Tag, soup: BeautifulSoup) -> bool:
        """Insert or remove a wrapper -- the cheapest way to break a structural path."""
        if self.rng.random() < 0.6:
            wrapper = soup.new_tag("div")
            wrapper["class"] = [f"wrap-{self._hash(5)}"]
            wrapper[TRACK] = self._new_track()
            el.wrap(wrapper)
            return True
        parent = el.parent
        if (parent is not None and parent.name == "div"
                and len(parent.find_all(True, recursive=False)) == 1):
            parent.unwrap()
            return True
        return False

    def _position_change(self, el: Tag) -> bool:
        parent = el.parent
        if parent is None:
            return False
        sibs = [s for s in parent.find_all(True, recursive=False) if s is not el]
        if not sibs:
            return False
        target = self.rng.choice(sibs)
        el.extract()
        target.insert_after(el)
        return True

    def _copy_change(self, el: Tag) -> bool:
        """Reword a control's own label; the element keeps its identity and stays the answer.

        The sub-types follow how reprobreak.copy_kind classifies text changes:
        case (only letter case changes), partial (one label contains the other:
        extended or shortened), substantial (shares a word, neither contains the
        other), replace (no shared word). A one-word label cannot have a
        substantial edit and gets a replacement instead.
        """
        txt = _ws(el.get_text(" ", strip=True))
        if not txt or len(txt) > 60:
            return False
        ko = bool(HANGUL.search(txt))
        words = txt.split()
        kind = self.rng.choices(self.copy_kinds, weights=self.copy_weights, k=1)[0]
        if kind == "substantial" and len(words) < 2:
            kind = "replace"
        new = None
        if kind == "replace":
            for a, b in SWAPS:
                if a.lower() in txt.lower():
                    new = re.sub(re.escape(a), b, txt, flags=re.I)
                    break
            if new is None:
                new = self.rng.choice(RELABEL_KO if ko else RELABEL_EN)
        elif kind == "substantial":
            pool = [w for w in (SUBST_KO if ko else SUBST_EN) if w.lower() not in txt.lower()]
            new = " ".join(words[:-1] + [self.rng.choice(pool)])
        elif kind == "partial":
            if len(words) > 1 and self.rng.random() < 0.5:
                new = " ".join(words[:-1])
            else:
                new = txt + self.rng.choice(SUFFIX_KO if ko else SUFFIX_EN)
        else:
            new = txt.upper() if txt != txt.upper() else txt.lower()
        if not new or _ws(new) == txt:
            return False
        # only the element's own text node; labelled children keep their text
        for node in list(el.find_all(string=True, recursive=False)):
            if node.strip():
                node.replace_with(new)
                return True
        if not el.find(True):
            el.string = new
            return True
        return False

    # -- deletion -------------------------------------------------------------
    @staticmethod
    def behaviour_key(el: Tag, base_url: str = ""):
        """What a control does, as far as static HTML can tell (mirrors retrieval.equivalent).

        Two controls with the same key are interchangeable for a recorded step, so
        deleting one while the other survives is not a removal. Links are keyed by
        their resolved destination; other controls by label plus id, test id or
        form action. A dead href ("#", javascript:) does not count.
        """
        href = str(el.get("href") or "").strip()
        if href and href != "#" and not href.lower().startswith("javascript:"):
            return ("href", urljoin(base_url, href))
        name = _norm(control_label(el))
        ident = str(el.get("id") or el.get("data-testid") or el.get("formaction") or "").strip()
        return (name, ident) if (name and ident) else None

    def delete_groups(self, soup: BeautifulSoup, rng: random.Random, ratio: float,
                      condition: str, deletable: set | None = None,
                      base_url: str = "") -> tuple[list, int]:
        """Remove whole behavioural groups until `ratio` of the labelled controls are gone.

        Only controls in `deletable` (the ones laid out on the unchanged render)
        are considered. A group is skipped if removing it would overshoot the
        budget, so large groups of repeated links are rarely removed.

        `uniform` picks groups at random. `lookalike` draws each pick from groups
        that leave a similar control behind (same tag, character-bigram Jaccard
        >= 0.3 on the label, within 100 positions in document order) with
        probability 0.7 while such groups remain, and protects that similar
        control's group from being deleted later.
        """
        if condition not in DELETION_CONDITIONS:
            raise ValueError(f"unknown deletion condition {condition!r}")
        cands = [el for el in soup.find_all(ACTIONABLE)
                 if el.get(TRACK) and control_label(el)
                 and (deletable is None or el[TRACK] in deletable)]
        n = int(len(cands) * ratio)
        if not n:
            return [], 0
        group_of, groups = {}, []
        index: dict = {}
        for i, el in enumerate(cands):
            key = self.behaviour_key(el, base_url)
            k = key if key else ("__solo__", i)
            if k not in index:
                index[k] = len(groups)
                groups.append([])
            groups[index[k]].append(i)
            group_of[i] = index[k]

        partners: dict = {g: set() for g in range(len(groups))}
        if condition == "lookalike":
            grams = [_bigrams(control_label(el)) for el in cands]
            for i, el in enumerate(cands):
                for j in range(max(0, i - 100), min(len(cands), i + 101)):
                    if group_of[j] == group_of[i] or cands[j].name != el.name:
                        continue
                    a, b = grams[i], grams[j]
                    if a and b and len(a & b) / len(a | b) >= 0.3:
                        partners[group_of[i]].add(group_of[j])

        look = [g for g in range(len(groups)) if partners[g]]
        rest = [g for g in range(len(groups)) if not partners[g]]
        rng.shuffle(look)
        rng.shuffle(rest)
        protected, gone, removed, n_groups = set(), [], 0, 0
        while removed < n and (look or rest):
            look = [g for g in look if g not in protected]
            rest = [g for g in rest if g not in protected]
            if not (look or rest):
                break
            from_look = condition == "lookalike" and look and (not rest or rng.random() < 0.7)
            g = (look if from_look else rest).pop()
            if removed + len(groups[g]) > n:
                continue
            if from_look:
                protected |= partners[g]
            for i in groups[g]:
                if not getattr(cands[i], "decomposed", False):
                    gone.append(cands[i][TRACK])
                    cands[i].decompose()
            removed += len(groups[g])
            n_groups += 1
        return gone, n_groups

    # -- driver -----------------------------------------------------------------
    def mutate(self, html: str, deletion_rng: random.Random, deletion: str = "uniform",
               delete_range: tuple = (0.03, 0.15), deletable: set | None = None,
               base_url: str = "") -> MutationResult:
        soup = BeautifulSoup(html, "html.parser")
        self._per_element = {}
        body = soup.body or soup
        for el in body.find_all(True):
            if not el.get(TRACK):
                el[TRACK] = self._new_track()

        bundle = self.rng.choices(self.bundles, weights=self.weights, k=1)[0]
        targets = [el for el in body.find_all(True) if el.name not in SKIP]
        if not targets:
            return MutationResult(html=str(soup), bundle=bundle)

        share = self.rng.uniform(0.25, 0.8)
        chosen = self.rng.sample(targets, max(1, int(len(targets) * share)))
        # make sure the edit reaches actionable controls, not only wrappers
        actionable = [el for el in targets if el.name in ACTIONABLE]
        if actionable:
            seen = {id(e) for e in chosen}
            forced = self.rng.sample(actionable, max(1, int(len(actionable) * share)))
            chosen.extend(e for e in forced if id(e) not in seen)

        ops = {
            "CLASS_CHURN": self._class_churn, "ID_CHANGE": self._id_change,
            "ID_REMOVED": self._id_removed, "ID_ADDED": self._id_added,
            "ATTR_CHANGE": self._attr_change, "TESTID_CHANGE": self._testid_change,
            "TAG_SWAP": self._tag_swap, "COPY_CHANGE": self._copy_change,
            "POSITION_CHANGE": self._position_change,
        }
        applied = set()
        for el in chosen:
            for axis in bundle:
                try:
                    if axis == "DEPTH_CHANGE":
                        changed = self._depth_change(el, soup)
                    else:
                        changed = ops[axis](el)
                except (ValueError, AttributeError, TypeError):
                    continue
                if changed:
                    applied.add(axis)
                    self._note(el, axis)

        ratio = deletion_rng.uniform(*delete_range)
        deleted, n_groups = self.delete_groups(soup, deletion_rng, ratio, deletion, deletable,
                                               base_url)
        return MutationResult(
            html=str(soup), bundle=bundle, applied=sorted(applied),
            per_element={k: sorted(v) for k, v in self._per_element.items()},
            deleted=deleted, n_deleted_groups=n_groups)
