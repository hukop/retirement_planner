"""
State Syncing Callbacks.

Binds the explicit Save buttons across the application to pull component State
from the DOM and synchronize it backward into the local `profile-store`.
"""

import dash
from dash import Input, Output, State, ALL
import dash_bootstrap_components as dbc
from typing import Optional

def _toast(msg: str, header: str = "Saved", icon: str = "success") -> dbc.Toast:
    return dbc.Toast(
        msg, header=header, icon=icon, duration=3000, is_open=True
    )

def register_forms_callbacks(app: dash.Dash):

    # ── 1. Profile Page Sync ─────────────────────────────────────────────
    @app.callback(
        Output("profile-store", "data", allow_duplicate=True),
        Output("toast-container", "children", allow_duplicate=True),
        Input("profile-save-btn", "n_clicks"),
        State("profile-store", "data"),

        # Self
        State("profile-self-name", "value"), State("profile-self-age", "value"),
        State("profile-self-retirement-age", "value"), State("profile-self-life-expectancy", "value"),
        State("profile-self-ss-benefit", "value"), State("profile-self-ss-claiming-age", "value"),

        # Spouse
        State("profile-spouse-name", "value"), State("profile-spouse-age", "value"),
        State("profile-spouse-retirement-age", "value"), State("profile-spouse-life-expectancy", "value"),
        State("profile-spouse-ss-benefit", "value"), State("profile-spouse-ss-claiming-age", "value"),

        # Globals
        State("profile-filing-status", "value"), State("profile-inflation-rate", "value"),

        # Monte Carlo
        State("profile-mc-seed", "value"),

        State("profile-mc-mean-return", "value"), State("profile-mc-std-dev", "value"),
        State("profile-mc-bond-return", "value"),
        prevent_initial_call=True
    )
    def sync_profile(
        n_clicks, profile_data,
        slf_n, slf_age, slf_ret, slf_life, slf_ss_ben, slf_ss_age,
        sp_n, sp_age, sp_ret, sp_life, sp_ss_ben, sp_ss_age,
        filing, inflation,
        mc_seed, mc_mean, mc_std, mc_bond
    ):
        if not profile_data: profile_data = {}

        profile_data.update({
            "filing_status": filing or "married_jointly",
            "inflation_rate_pct": float(inflation or 3.0),
        })

        # Merge with existing values so that debounced inputs that haven't
        # reported their new value yet don't wipe out previously saved data.
        existing_self = profile_data.get("self_person", {})
        new_self = {
            "name": slf_n, "current_age": slf_age, "retirement_age": slf_ret,
            "life_expectancy": slf_life, "ss_monthly_benefit": slf_ss_ben,
            "ss_claiming_age": slf_ss_age,
        }
        profile_data["self_person"] = {**existing_self, **{k: v for k, v in new_self.items() if v is not None}}

        existing_spouse = profile_data.get("spouse", {})
        new_spouse = {
            "name": sp_n, "current_age": sp_age, "retirement_age": sp_ret,
            "life_expectancy": sp_life, "ss_monthly_benefit": sp_ss_ben,
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
            "bond_std_dev_pct": 5.0, # default
            "random_seed": int(mc_seed) if mc_seed is not None and str(mc_seed).strip() != "" else None,
        }


        return profile_data, _toast("Profile settings updated locally.")

    # ── Reset Profile ────────────────────────────────────────────────────
    @app.callback(
        Output("profile-store", "data", allow_duplicate=True),
        Output("toast-container", "children", allow_duplicate=True),
        Input("profile-reset-btn", "n_clicks"),
        prevent_initial_call=True,
    )
    def reset_profile(n_clicks):
        """Reset the profile store to the built-in sample plan."""
        if not n_clicks:
            raise dash.exceptions.PreventUpdate
        from engine.models import PlanProfile
        sample = PlanProfile.sample()
        return sample.to_dict(), _toast(
            "Profile reset to sample defaults.", header="Reset", icon="info"
        )

    # ── Reactive UI Updates (Profile Summary) ───────────────────────────
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

    # ── 2. Income Page Sync ──────────────────────────────────────────────
    @app.callback(
        Output("profile-store", "data", allow_duplicate=True),
        Output("toast-container", "children", allow_duplicate=True),
        Input("income-save-btn", "n_clicks"),
        State("profile-store", "data"),
        State({"type": "income-name", "index": ALL}, "value"),
        State({"type": "income-owner", "index": ALL}, "value"),
        State({"type": "income-amount", "index": ALL}, "value"),
        State({"type": "income-raise", "index": ALL}, "value"),
        State({"type": "income-start-age", "index": ALL}, "value"),
        State({"type": "income-end-age", "index": ALL}, "value"),
        State({"type": "income-item", "index": ALL}, "style"),
        prevent_initial_call=True
    )
    def sync_income(n_clicks, profile_data, names, owners, amounts, raises, starts, ends, styles):
        if not dash.ctx.triggered_id or not profile_data:
            raise dash.exceptions.PreventUpdate

        incomes = []
        for i in range(len(names)):
            if styles[i] and styles[i].get("display") == "none":
                continue # Skip soft-deleted items

            incomes.append({
                "name": names[i], "owner": owners[i],
                "annual_amount": amounts[i] or 0.0, "annual_raise_pct": raises[i] or 0.0,
                "start_age": starts[i] or 0, "end_age": ends[i] or 0,
            })
        profile_data["incomes"] = incomes
        return profile_data, _toast("Income targets synced.")

    # ── 3. Expenses Page Sync ────────────────────────────────────────────
    @app.callback(
        Output("profile-store", "data", allow_duplicate=True),
        Output("toast-container", "children", allow_duplicate=True),
        Input("expenses-save-btn", "n_clicks"),
        State("profile-store", "data"),
        # Recurring expenses
        State({"type": "expense-name",       "index": ALL}, "value"),
        State({"type": "expense-category",   "index": ALL}, "value"),
        State({"type": "expense-amount",     "index": ALL}, "value"),
        State({"type": "expense-retire-pct", "index": ALL}, "value"),
        State({"type": "expense-inflation",  "index": ALL}, "value"),
        State({"type": "expense-item",       "index": ALL}, "style"),
        # One-time expenses
        State({"type": "otex-name",      "index": ALL}, "value"),
        State({"type": "otex-amount",    "index": ALL}, "value"),
        State({"type": "otex-year",      "index": ALL}, "value"),
        State({"type": "otex-inflation", "index": ALL}, "value"),
        State({"type": "otex-item",      "index": ALL}, "style"),
        prevent_initial_call=True
    )
    def sync_expenses(
        n_clicks, profile_data,
        rec_names, rec_cats, rec_amts, rec_pcts, rec_infs, rec_styles,
        otex_names, otex_amts, otex_years, otex_infs, otex_styles,
    ):
        if not dash.ctx.triggered_id or not profile_data:
            raise dash.exceptions.PreventUpdate

        # Regular Expenses — name field is now read from the form directly
        expenses = []
        for i in range(len(rec_cats)):
            if rec_styles[i] and rec_styles[i].get("display") == "none":
                continue  # Skip soft-deleted items

            # Fall back to category if the user left the name blank
            name = (rec_names[i] or "").strip() or rec_cats[i] or "Expense"
            expenses.append({
                "name": name,
                "category": rec_cats[i], "monthly_amount": rec_amts[i] or 0.0,
                "retirement_pct": float(rec_pcts[i] or 100.0),
                "inflation_adjusted": bool(rec_infs[i]),
            })

        one_times = []
        for i in range(len(otex_names)):
            if otex_styles[i] and otex_styles[i].get("display") == "none":
                continue

            one_times.append({
                "name": otex_names[i], "amount": float(otex_amts[i] or 0.0),
                "year": int(otex_years[i] or 2030), "inflation_adjusted": bool(otex_infs[i])
            })

        profile_data["expenses"] = expenses
        profile_data["one_time_expenses"] = one_times
        return profile_data, _toast("Expense budgets synced.")

    # ── 4. Investments Page Sync ─────────────────────────────────────────
    @app.callback(
        Output("profile-store", "data", allow_duplicate=True),
        Output("toast-container", "children", allow_duplicate=True),
        Input("investments-save-btn", "n_clicks"),
        State("profile-store", "data"),
        State({"type": "acc-name", "index": ALL}, "value"),
        State({"type": "acc-type", "index": ALL}, "value"),
        State({"type": "acc-owner", "index": ALL}, "value"),
        State({"type": "acc-balance", "index": ALL}, "value"),
        State({"type": "acc-return", "index": ALL}, "value"),
        State({"type": "acc-contrib", "index": ALL}, "value"),
        State({"type": "acc-cost-basis", "index": ALL}, "value"),
        State({"type": "acc-match", "index": ALL}, "value"),
        State({"type": "account-item", "index": ALL}, "style"),
        prevent_initial_call=True
    )
    def sync_investments(n_clicks, profile_data, names, types, owners, bals, rets, contribs, costs, matches, styles):
        if not dash.ctx.triggered_id or not profile_data:
            raise dash.exceptions.PreventUpdate

        accounts = []
        for i in range(len(names)):
            if styles[i] and styles[i].get("display") == "none":
                continue # Skip soft-deleted items

            accounts.append({
                "name": names[i], "account_type": types[i], "owner": owners[i],
                "balance": float(bals[i] or 0), "annual_return_pct": float(rets[i] or 0),
                "annual_contribution": float(contribs[i] or 0), 
                "cost_basis": float(costs[i] or 0) if types[i] == "brokerage" else 0.0,
                "employer_match": float(matches[i] or 0) if types[i] == "401k" else 0.0
            })
        profile_data["accounts"] = accounts
        return profile_data, _toast("Portfolio structure synced.")

    # ── 5. Real Estate Page Sync ─────────────────────────────────────────
    @app.callback(
        Output("profile-store", "data", allow_duplicate=True),
        Output("toast-container", "children", allow_duplicate=True),
        Input("real-estate-save-btn", "n_clicks"),
        State("profile-store", "data"),
        State({"type": "prop-name", "index": ALL}, "value"),
        State({"type": "prop-type", "index": ALL}, "value"),
        State({"type": "prop-value", "index": ALL}, "value"),
        State({"type": "prop-appreciation", "index": ALL}, "value"),
        State({"type": "prop-has-mortgage", "index": ALL}, "value"),
        State({"type": "prop-mortgage-bal", "index": ALL}, "value"),
        State({"type": "prop-mortgage-rate", "index": ALL}, "value"),
        State({"type": "prop-mortgage-years", "index": ALL}, "value"),
        State({"type": "prop-mortgage-payment", "index": ALL}, "value"),
        State({"type": "prop-rent-inc", "index": ALL}, "value"),
        State({"type": "prop-rent-exp", "index": ALL}, "value"),
        State({"type": "prop-rent-inflation", "index": ALL}, "value"),
        State({"type": "property-item", "index": ALL}, "style"),
        prevent_initial_call=True
    )
    def sync_real_estate(
        n_clicks, profile_data,
        names, types, vals, apprs, has_morts, morts, rates, yrs, pmts, rents, exps, rent_infls, styles
    ):
        if not dash.ctx.triggered_id or not profile_data:
            raise dash.exceptions.PreventUpdate

        properties = []
        for i in range(len(names)):
            if styles[i] and styles[i].get("display") == "none":
                continue # Skip soft-deleted items

            properties.append({
                "name": names[i], "property_type": types[i],
                "current_value": float(vals[i] or 0), "appreciation_rate_pct": float(apprs[i] or 0),
                "mortgage_balance": float(morts[i] or 0) if has_morts[i] == "yes" else 0.0,
                "monthly_payment": float(pmts[i] or 0) if has_morts[i] == "yes" else 0.0,
                "mortgage_rate_pct": float(rates[i] or 0) if has_morts[i] == "yes" else 0.0,
                "years_remaining": int(yrs[i] or 0) if has_morts[i] == "yes" else 0,
                "monthly_rental_income": float(rents[i] or 0),
                "monthly_expenses": float(exps[i] or 0),
                "rental_inflation_rate_pct": float(rent_infls[i] or 3.0)
            })
        profile_data["properties"] = properties
        return profile_data, _toast("Property ledger synced.")
