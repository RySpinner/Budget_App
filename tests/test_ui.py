from app.ui import md


def test_md_escapes_dollar_signs():
    assert md("$5.00 of $10.00") == "\\$5.00 of \\$10.00"
    assert md(ValueError("x")) == "x"
