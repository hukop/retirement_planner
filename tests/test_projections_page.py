import pandas as pd

from ui.pages.projections import _with_cashflow_check_columns, layout


def _component_text(component):
    if component is None:
        return []
    if isinstance(component, str):
        return [component]
    children = getattr(component, "children", None)
    if isinstance(children, (list, tuple)):
        text = []
        for child in children:
            text.extend(_component_text(child))
        return text
    return _component_text(children)


def test_projection_page_recomputes_cashflow_check_for_legacy_data():
    legacy = pd.DataFrame([
        {
            "year": 2030,
            "income_total": 100_000.0,
            "withdrawal_total": 10_000.0,
            "contrib_employer_match": 5_000.0,
            "expense_total": 60_000.0,
            "tax_annual_est": 20_000.0,
            "contrib_total": 35_000.0,
        },
        {
            "year": 2031,
            "income_total": 100_000.0,
            "withdrawal_total": 10_000.0,
            "contrib_employer_match": 5_000.0,
            "expense_total": 60_000.0,
            "tax_annual_est": 20_000.0,
            "contrib_total": 30_000.0,
        },
    ])

    checked = _with_cashflow_check_columns(legacy)

    assert checked.loc[0, "cashflow_check"] == 0.0
    assert bool(checked.loc[0, "cashflow_check_ok"]) is True
    assert checked.loc[1, "cashflow_check"] == 5_000.0
    assert bool(checked.loc[1, "cashflow_check_ok"]) is False


def test_projection_layout_includes_cashflow_check_panel():
    projection_data = [
        {
            "year": 2030,
            "income_total": 100_000.0,
            "income_salary_self": 100_000.0,
            "income_salary_spouse": 0.0,
            "income_ss_self": 0.0,
            "income_ss_spouse": 0.0,
            "income_rental_net": 0.0,
            "withdrawal_total": 0.0,
            "expense_total": 70_000.0,
            "tax_annual_est": 20_000.0,
            "contrib_total": 10_000.0,
            "contrib_employer_match": 0.0,
            "surplus_deficit": 10_000.0,
            "balance_investment_total": 500_000.0,
            "equity_re_total": 0.0,
            "net_worth_eoy": 500_000.0,
        }
    ]

    page = layout(projection_data=projection_data)

    assert "Cashflow Check" in _component_text(page)
    assert "All years pass" in _component_text(page)
