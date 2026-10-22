from selector_heal.reprobreak import classify, copy_kind


def test_test_id_changes_are_not_tag_swaps():
    assert classify("page.getByTestId('save')", "page.getByTestId('submit')") == {"TESTID_CHANGE"}


def test_attribute_values_are_not_labels():
    assert classify("cy.get('[name=\"email\"]')", "cy.get('[name=\"username\"]')") == {"ATTR_CHANGE"}


def test_role_name_change_is_a_copy_change():
    assert classify("page.getByRole('button', { name: 'Save' })",
                    "page.getByRole('button', { name: 'Save changes' })") == {"COPY_CHANGE"}


def test_mixed_quotes_and_depth():
    assert classify("page.waitForSelector('[role=\"tooltip\"]')",
                    "page.waitForSelector('[role=\"dialog\"]')") == {"ATTR_CHANGE"}
    assert classify("cy.get('#a > .b')", "cy.get('#a > div > .b')") == {"DEPTH_CHANGE"}


def test_strategy_switch_is_not_a_copy_change():
    axes = classify("cy.get('.save-btn')", "page.getByText('Save')")
    assert "COPY_CHANGE" not in axes and "STRATEGY_CSS_TO_TEXT" in axes


def test_copy_kinds():
    assert copy_kind({"Save"}, {"SAVE"}) == "case"
    assert copy_kind({"Save"}, {"Save changes"}) == "partial"
    assert copy_kind({"Save draft"}, {"Save changes"}) == "substantial"
    assert copy_kind({"Download"}, {"Export"}) == "replace"
