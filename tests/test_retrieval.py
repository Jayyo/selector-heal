from selector_heal.retrieval import equivalent


def el(**kw):
    base = {"tag": "a", "name": "Docs", "text": "Docs", "href": "", "id": "", "testid": "",
            "testidAttr": ""}
    base.update(kw)
    return base


def test_same_label_and_destination():
    assert equivalent(el(href="/docs"), el(href="/docs", name="DOCS"))


def test_destinations_and_ids_are_case_sensitive():
    assert not equivalent(el(href="/item/A"), el(href="/item/a"))
    assert not equivalent(el(id="row-A"), el(id="row-a"))


def test_dead_links_and_bare_buttons_are_not_merged():
    assert not equivalent(el(href="#"), el(href="#"))
    assert not equivalent(el(href="javascript:void(0)"), el(href="javascript:void(0)"))
    assert not equivalent(el(tag="button", name="Delete"), el(tag="button", name="Delete"))


def test_test_ids_compare_with_their_attribute():
    assert equivalent(el(testid="save", testidAttr="data-testid"),
                      el(testid="save", testidAttr="data-testid"))
    assert not equivalent(el(testid="save", testidAttr="data-testid"),
                          el(testid="save", testidAttr="data-cy"))


def test_links_to_the_same_place_are_equivalent_whatever_the_label():
    card_image = el(name="", text="", href="/p/42", hrefAbs="https://shop.test/p/42")
    card_title = el(name="Blue mug", href="/p/42", hrefAbs="https://shop.test/p/42")
    assert equivalent(card_title, card_image)
    # resolved destinations decide, so relative and absolute spellings match
    assert equivalent(el(href="/docs", hrefAbs="https://a.test/docs"),
                      el(name="Read the docs", href="https://a.test/docs",
                         hrefAbs="https://a.test/docs"))
