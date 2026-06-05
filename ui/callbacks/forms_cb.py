"""
State Syncing Callbacks.

ALL tabs now auto-save on every input change (no button click needed).
Save buttons have been removed from all pages since data persists instantly.
"""

import dash
from dash import Input, Output, State, ALL
import dash_bootstrap_components as dbc
from typing import Optional

def _toast(msg: str, header: str = "Saved", icon: str = "success") -> dbc.Toast:
    return dbc.Toast(
        msg, header=header, icon=icon, duration=3000, is_open=True
    )


# ─────────────────────────────────────────────────────────────────────────
# Shared profile-merge helper
# ─────────────────────────────────────────────────────────────────────────
def _merge_profile_data(profile_data: dict | None, filing: str, inflation: float,
                        slf_n, slf_yr, slf_mo, slf_ret_yr, slf_life, slf_ss_ben, slf_ss_age,
                        sp_n, sp_yr, sp_mo, sp_ret_yr, sp_life, sp_ss_ben, sp_ss_age,
                        mc_seed, mc_mean, mc_std, mc_bond) -> dict:
    """Merge all profile input values into profile_data dict."""
    if not profile_data:
        profile_data = {}

    profile_data.update({
        "filing_status": filing or "married_jointly",
        "inflation_rate_pct": float(inflation or 3.0),
    })

    # Merge self person
    existing_self = profile_data.get("self_person", {})
    new_self = {
        "name": slf_n,
        "birth_year": slf_yr,
        "birth_month": slf_mo,
        "retirement_year": slf_ret_yr,
        "life_expectancy": slf_life,
        "ss_monthly_benefit": slf_ss_ben,
        "ss_claiming_age": slf_ss_age,
    }
    profile_data["self_person"] = {**existing_self, **{k: v for k, v in new_self.items() if v is not None}}

    # Merge spouse person
    existing_spouse = profile_data.get("spouse", {})
    new_spouse = {
        "name": sp_n,
        "birth_year": sp_yr,
        "birth_month": sp_mo,
        "retirement_year": sp_ret_yr,
        "life_expectancy": sp_life,
        "ss_monthly_benefit": sp_ss_ben,
        "ss_claiming_age": sp_ss_age,
    }
    profile_data["spouse"] = {**existing_spouse, **{k: v for k, v in new_spouse.items() if v is not None}}

    # Monte Carlo
    existing_mc = profile_data.get("monte_carlo", {})
    profile_data["monte_carlo"] = {
        **existing_mc,
        "mean_return_pct": float(mc_mean or 7.0),
        "std_dev_pct": float(mc_std or 15.0),
        "bond_mean_return_pct": float(mc_bond or 4.0),
        "bond_std_dev_pct": 5.0,  # default
        "random_seed": int(mc_seed) if mc_seed is not None and str(mc_seed).strip() != "" else None,
    }

    return profile_data


# ─────────────────────────────────────────────────────────────────────────
# Shared list-build helpers for income/expenses/investments/real-estate
# ─────────────────────────────────────────────────────────────────────────
def _build_incomes(names, owners, amounts, raises, starts, ends, styles) -> list:
    """Build incomes list from form inputs, skipping deleted items."""
    incomes = []
    for i in range(len(names)):
        if styles and i < len(styles) and styles[i] and styles[i].get("display") == "none":
            continue
        incomes.append({
            "name": names[i] if i < len(names) else "",
            "owner": owners[i] if i < len(owners) else "self",
            "annual_amount": (amounts[i] if i < len(amounts) else 0) or 0.0,
            "annual_raise_pct": (raises[i] if i < len(raises) else 0) or 0.0,
            "start_age": (starts[i] if i < len(starts) else 0) or 0,
            "end_age": (ends[i] if i < len(ends) else 0) or 0,
        })
    return incomes


def _build_expenses(rec_names, rec_cats, rec_amts, rec_factors, rec_infs, rec_styles,
                    otex_names, otex_amts, otex_years, otex_infs, otex_styles):
    """Build expenses + one_time_expenses lists from form inputs."""
    expenses = []
    for i in range(len(rec_cats)):
        if rec_styles and i < len(rec_styles) and rec_styles[i] and rec_styles[i].get("display") == "none":
            continue
        name = ((rec_names[i] if i < len(rec_names) else "") or "").strip()
        if not name:
            name = (rec_cats[i] if i < len(rec_cats) else "other") or "other"

        # Handle new retirement scaling: factor (1.0 = no scaling, 0.5 = half, 2.0 = double)
        # The UI disables the factor input when checkbox is unchecked, so it defaults to 1.0
        factor = float((rec_factors[i] if i < len(rec_factors) else 1.0) or 1.0)

        expenses.append({
            "name": name,
            "category": rec_cats[i] if i < len(rec_cats) else "other",
            "monthly_amount": (rec_amts[i] if i < len(rec_amts) else 0) or 0.0,
            "retirement_factor": factor,
            "inflation_adjusted": bool((rec_infs[i] if i < len(rec_infs) else True)),
        })

    one_times = []
    for i in range(len(otex_names)):
        if otex_styles and i < len(otex_styles) and otex_styles[i] and otex_styles[i].get("display") == "none":
            continue
        one_times.append({
            "name": otex_names[i] if i < len(otex_names) else "",
            "amount": float((otex_amts[i] if i < len(otex_amts) else 0) or 0.0),
            "year": int((otex_years[i] if i < len(otex_years) else 2030) or 2030),
            "inflation_adjusted": bool((otex_infs[i] if i < len(otex_infs) else True)),
        })
    return expenses, one_times


def _build_accounts(names, types, owners, bals, rets, contribs, costs, matches, styles) -> list:
    """Build accounts list from form inputs."""
    accounts = []
    for i in range(len(names)):
        if styles and i < len(styles) and styles[i] and styles[i].get("display") == "none":
            continue
        acc_type = types[i] if i < len(types) else "brokerage"
        accounts.append({
            "name": names[i] if i < len(names) else "",
            "account_type": acc_type,
            "owner": owners[i] if i < len(owners) else "joint",
            "balance": float((bals[i] if i < len(bals) else 0) or 0),
            "annual_return_pct": float((rets[i] if i < len(rets) else 0) or 0),
            "annual_contribution": float((contribs[i] if i < len(contribs) else 0) or 0),
            "cost_basis": float((costs[i] if i < len(costs) else 0) or 0) if acc_type == "brokerage" else 0.0,
            "employer_match": float((matches[i] if i < len(matches) else 0) or 0) if acc_type == "401k" else 0.0,
        })
    return accounts


def _build_properties(names, types, vals, apprs, has_morts, morts, rates, yrs, pmts, rents, exps, rent_infls, styles) -> list:
    """Build properties list from form inputs."""
    properties = []
    for i in range(len(names)):
        if styles and i < len(styles) and styles[i] and styles[i].get("display") == "none":
            continue
        has_mort = (has_morts[i] if i < len(has_morts) else "no") or "no"
        properties.append({
            "name": names[i] if i < len(names) else "",
            "property_type": types[i] if i < len(types) else "primary",
            "current_value": float((vals[i] if i < len(vals) else 0) or 0),
            "appreciation_rate_pct": float((apprs[i] if i < len(apprs) else 0) or 0),
            "mortgage_balance": float((morts[i] if i < len(morts) else 0) or 0) if has_mort == "yes" else 0.0,
            "monthly_payment": float((pmts[i] if i < len(pmts) else 0) or 0) if has_mort == "yes" else 0.0,
            "mortgage_rate_pct": float((rates[i] if i < len(rates) else 0) or 0) if has_mort == "yes" else 0.0,
            "years_remaining": int((yrs[i] if i < len(yrs) else 0) or 0) if has_mort == "yes" else 0,
            "monthly_rental_income": float((rents[i] if i < len(rents) else 0) or 0),
            "monthly_expenses": float((exps[i] if i < len(exps) else 0) or 0),
            "rental_inflation_rate_pct": float((rent_infls[i] if i < len(rent_infls) else 3.0) or 3.0),
        })
    return properties


# ─────────────────────────────────────────────────────────────────────────
# Registration
# ─────────────────────────────────────────────────────────────────────────
def register_forms_callbacks(app: dash.Dash):

    # ═══════════════════════════════════════════════════════════════════════
    # 1. PROFILE PAGE — auto-save on every input change
    # ═══════════════════════════════════════════════════════════════════════

    @app.callback(
        Output("profile-store", "data", allow_duplicate=True),
        # Self
        Input("profile-self-name", "value"),
        Input("profile-self-birth-year", "value"),
        Input("profile-self-birth-month", "value"),
        Input("profile-self-retirement-year", "value"),
        Input("profile-self-life-expectancy", "value"),
        Input("profile-self-ss-benefit", "value"),
        Input("profile-self-ss-claiming-age", "value"),
        # Spouse
        Input("profile-spouse-name", "value"),
        Input("profile-spouse-birth-year", "value"),
        Input("profile-spouse-birth-month", "value"),
        Input("profile-spouse-retirement-year", "value"),
        Input("profile-spouse-life-expectancy", "value"),
        Input("profile-spouse-ss-benefit", "value"),
        Input("profile-spouse-ss-claiming-age", "value"),
        # Globals
        Input("profile-filing-status", "value"),
        Input("profile-inflation-rate", "value"),
        # Monte Carlo
        Input("profile-mc-seed", "value"),
        Input("profile-mc-mean-return", "value"),
        Input("profile-mc-std-dev", "value"),
        Input("profile-mc-bond-return", "value"),
        # State: current store
        State("profile-store", "data"),
        prevent_initial_call=True,
    )
    def auto_sync_profile(
        slf_n, slf_yr, slf_mo, slf_ret_yr, slf_life, slf_ss_ben, slf_ss_age,
        sp_n, sp_yr, sp_mo, sp_ret_yr, sp_life, sp_ss_ben, sp_ss_age,
        filing, inflation,
        mc_seed, mc_mean, mc_std, mc_bond,
        profile_data,
    ):
        return _merge_profile_data(
            profile_data, filing, inflation,
            slf_n, slf_yr, slf_mo, slf_ret_yr, slf_life, slf_ss_ben, slf_ss_age,
            sp_n, sp_yr, sp_mo, sp_ret_yr, sp_life, sp_ss_ben, sp_ss_age,
            mc_seed, mc_mean, mc_std, mc_bond,
        )

    # ── Reset Profile ────────────────────────────────────────────────────
    @app.callback(
        Output("profile-store", "data", allow_duplicate=True),
        Output("toast-container", "children", allow_duplicate=True),
        Input("profile-reset-btn", "n_clicks"),
        prevent_initial_call=True,
    )
    def reset_profile(n_clicks):
        if not n_clicks:
            raise dash.exceptions.PreventUpdate
        from engine.models import PlanProfile
        sample = PlanProfile.sample()
        return sample.to_dict(), _toast(
            "Profile reset to sample defaults.", header="Reset", icon="info"
        )

    # ── Reactive Profile Summary ─────────────────────────────────────────
    @app.callback(
        Output("profile-summary-container", "children"),
        Input("profile-store", "data"),
        prevent_initial_call=False
    )
    def update_profile_summary(profile_data):
        if not profile_data:
            raise dash.exceptions.PreventUpdate
        from engine.models import PlanProfile
        from ui.pages.profile import _profile_summary
        profile = PlanProfile.from_dict(profile_data)
        return _profile_summary(profile)


    # ═══════════════════════════════════════════════════════════════════════
    # 2. INCOME PAGE — auto-save on every input change
    # ═══════════════════════════════════════════════════════════════════════

    @app.callback(
        Output("profile-store", "data", allow_duplicate=True),
        Input({"type": "income-name", "index": ALL}, "value"),
        Input({"type": "income-owner", "index": ALL}, "value"),
        Input({"type": "income-amount", "index": ALL}, "value"),
        Input({"type": "income-raise", "index": ALL}, "value"),
        Input({"type": "income-start-age", "index": ALL}, "value"),
        Input({"type": "income-end-age", "index": ALL}, "value"),
        State({"type": "income-item", "index": ALL}, "style"),
        State("profile-store", "data"),
        prevent_initial_call=True,
    )
    def auto_sync_income(names, owners, amounts, raises, starts, ends, styles, profile_data):
        if not profile_data:
            raise dash.exceptions.PreventUpdate

        # Prevent clearing incomes when form has no items rendered
        # (this happens when form is loading or re-rendering before items populate)
        names_list = names or []
        if not names_list and profile_data.get("incomes"):
            # Form has no items but profile has incomes - don't clear them
            raise dash.exceptions.PreventUpdate

        profile_data["incomes"] = _build_incomes(names_list, owners or [], amounts or [],
                                                  raises or [], starts or [], ends or [],
                                                  styles or [])
        return profile_data

    # ═══════════════════════════════════════════════════════════════════════
    # 3. EXPENSES PAGE — auto-save on every input change
    # ═══════════════════════════════════════════════════════════════════════

    @app.callback(
        Output("profile-store", "data", allow_duplicate=True),
        Input({"type": "expense-name",          "index": ALL}, "value"),
        Input({"type": "expense-category",      "index": ALL}, "value"),
        Input({"type": "expense-amount",        "index": ALL}, "value"),
        Input({"type": "expense-retire-factor", "index": ALL}, "value"),
        Input({"type": "expense-inflation",     "index": ALL}, "value"),
        Input({"type": "otex-name",             "index": ALL}, "value"),
        Input({"type": "otex-amount",           "index": ALL}, "value"),
        Input({"type": "otex-year",             "index": ALL}, "value"),
        Input({"type": "otex-inflation",        "index": ALL}, "value"),
        State({"type": "expense-item", "index": ALL}, "style"),
        State({"type": "otex-item",    "index": ALL}, "style"),
        State("profile-store", "data"),
        prevent_initial_call=True,
    )
    def auto_sync_expenses(
        rec_names, rec_cats, rec_amts, rec_factors, rec_infs,
        otex_names, otex_amts, otex_years, otex_infs,
        rec_styles, otex_styles, profile_data,
    ):
        if not profile_data:
            raise dash.exceptions.PreventUpdate

        # Prevent clearing expenses when form has no items rendered
        # (this happens when form is loading or re-rendering before items populate)
        rec_cats_list = rec_cats or []
        otex_names_list = otex_names or []
        if not rec_cats_list and not otex_names_list and profile_data.get("expenses"):
            # Form has no items but profile has expenses - don't clear them
            raise dash.exceptions.PreventUpdate

        expenses, one_times = _build_expenses(
            rec_names or [], rec_cats_list, rec_amts or [], rec_factors or [], rec_infs or [], rec_styles or [],
            otex_names_list, otex_amts or [], otex_years or [], otex_infs or [], otex_styles or [],
        )
        profile_data["expenses"] = expenses
        profile_data["one_time_expenses"] = one_times
        return profile_data

    @app.callback(
        Output("profile-store", "data", allow_duplicate=True),
        Output("onetime-expenses-container", "children", allow_duplicate=True),
        Output("toast-container", "children", allow_duplicate=True),
        Input("btn-sort-otex-year", "n_clicks"),
        State({"type": "otex-name",      "index": ALL}, "value"),
        State({"type": "otex-amount",    "index": ALL}, "value"),
        State({"type": "otex-year",      "index": ALL}, "value"),
        State({"type": "otex-inflation", "index": ALL}, "value"),
        State({"type": "otex-item",      "index": ALL}, "style"),
        State("profile-store", "data"),
        prevent_initial_call=True,
    )
    def sort_one_time_expenses_by_year(
        n_clicks, otex_names, otex_amts, otex_years, otex_infs, otex_styles, profile_data
    ):
        if not n_clicks or not profile_data:
            raise dash.exceptions.PreventUpdate

        if otex_names:
            _, one_times = _build_expenses(
                [], [], [], [], [], [],
                otex_names or [], otex_amts or [], otex_years or [], otex_infs or [], otex_styles or [],
            )
        else:
            one_times = profile_data.get("one_time_expenses") or []

        if not one_times:
            return dash.no_update, dash.no_update, _toast(
                "No one-time expenses to sort.",
                header="Sort",
                icon="info",
            )

        def sort_year(exp):
            if not isinstance(exp, dict):
                return 2030
            try:
                return int(exp.get("year") or 2030)
            except (TypeError, ValueError):
                return 2030

        sorted_one_times = sorted(one_times, key=sort_year)
        profile_data = {**profile_data, "one_time_expenses": sorted_one_times}

        from ui.pages.expenses import _one_time_expense_item
        children = [
            _one_time_expense_item(idx, otex)
            for idx, otex in enumerate(sorted_one_times)
        ]

        return profile_data, children, _toast(
            "One-time expenses sorted by target year.",
            header="Sorted",
            icon="success",
        )

    # ═══════════════════════════════════════════════════════════════════════
    # 4. INVESTMENTS PAGE — auto-save on every input change
    # ═══════════════════════════════════════════════════════════════════════

    @app.callback(
        Output("profile-store", "data", allow_duplicate=True),
        Input({"type": "acc-name", "index": ALL}, "value"),
        Input({"type": "acc-type", "index": ALL}, "value"),
        Input({"type": "acc-owner", "index": ALL}, "value"),
        Input({"type": "acc-balance", "index": ALL}, "value"),
        Input({"type": "acc-return", "index": ALL}, "value"),
        Input({"type": "acc-contrib", "index": ALL}, "value"),
        Input({"type": "acc-cost-basis", "index": ALL}, "value"),
        Input({"type": "acc-match", "index": ALL}, "value"),
        State({"type": "account-item", "index": ALL}, "style"),
        State("profile-store", "data"),
        prevent_initial_call=True,
    )
    def auto_sync_investments(names, types, owners, bals, rets, contribs, costs, matches, styles, profile_data):
        if not profile_data:
            raise dash.exceptions.PreventUpdate

        # Prevent clearing accounts when form has no items rendered
        names_list = names or []
        if not names_list and profile_data.get("accounts"):
            # Form has no items but profile has accounts - don't clear them
            raise dash.exceptions.PreventUpdate

        profile_data["accounts"] = _build_accounts(
            names_list, types or [], owners or [], bals or [], rets or [],
            contribs or [], costs or [], matches or [], styles or [],
        )
        return profile_data

    # ═══════════════════════════════════════════════════════════════════════
    # 5. REAL ESTATE PAGE — auto-save on every input change
    # ═══════════════════════════════════════════════════════════════════════

    @app.callback(
        Output("profile-store", "data", allow_duplicate=True),
        Input({"type": "prop-name", "index": ALL}, "value"),
        Input({"type": "prop-type", "index": ALL}, "value"),
        Input({"type": "prop-value", "index": ALL}, "value"),
        Input({"type": "prop-appreciation", "index": ALL}, "value"),
        Input({"type": "prop-has-mortgage", "index": ALL}, "value"),
        Input({"type": "prop-mortgage-bal", "index": ALL}, "value"),
        Input({"type": "prop-mortgage-rate", "index": ALL}, "value"),
        Input({"type": "prop-mortgage-years", "index": ALL}, "value"),
        Input({"type": "prop-mortgage-payment", "index": ALL}, "value"),
        Input({"type": "prop-rent-inc", "index": ALL}, "value"),
        Input({"type": "prop-rent-exp", "index": ALL}, "value"),
        Input({"type": "prop-rent-inflation", "index": ALL}, "value"),
        State({"type": "property-item", "index": ALL}, "style"),
        State("profile-store", "data"),
        prevent_initial_call=True,
    )
    def auto_sync_real_estate(
        names, types, vals, apprs, has_morts, morts, rates, yrs, pmts,
        rents, exps, rent_infls, styles, profile_data,
    ):
        if not profile_data:
            raise dash.exceptions.PreventUpdate

        # Prevent clearing properties when form has no items rendered
        names_list = names or []
        if not names_list and profile_data.get("properties"):
            # Form has no items but profile has properties - don't clear them
            raise dash.exceptions.PreventUpdate

        profile_data["properties"] = _build_properties(
            names_list, types or [], vals or [], apprs or [], has_morts or [],
            morts or [], rates or [], yrs or [], pmts or [],
            rents or [], exps or [], rent_infls or [], styles or [],
        )
        return profile_data
