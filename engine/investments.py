"""
Investment account simulation engine.

Responsibilities
----------------
- Maintain a mutable ``AccountState`` for each ``InvestmentAccount`` during
  a projection run (the source ``InvestmentAccount`` dataclass is read-only).
- Apply monthly compound growth.
- Apply monthly contributions + employer match while the owner is working.
- Execute withdrawals with precise proportional cost-basis tracking for
  brokerage accounts → enables exact LTCG calculation in the tax engine.
- Return structured ``WithdrawalResult`` objects so the projection engine can
  pass the correct income type to ``engine.taxes``.

Cost-Basis Tracking (brokerage / savings)
------------------------------------------
The basis pool grows with every dollar deposited (contributions + reinvested
dividends counted via the initial balance/basis split).  On each withdrawal the
gain fraction is computed as:

    gain_fraction = (balance - cost_basis) / balance   [clamped to [0, 1]]
    capital_gain  = withdrawal_amount * gain_fraction
    basis_used    = withdrawal_amount - capital_gain

This is the "average-cost proportional" method — it is well-suited for
planning purposes even if real brokerage accounts use FIFO or specific-lot
accounting.

For tax-deferred accounts (401k, trad_ira) the entire withdrawal is ordinary
income regardless of basis, so we don't track basis for those.
For tax-free accounts (roth_ira, roth_401k, hsa) the entire withdrawal is
tax-free, so again basis tracking is informational only.

Fallback
--------
If the user leaves ``InvestmentAccount.cost_basis`` as ``None``, the engine
initialises cost_basis = 50 % of starting balance.  This is a conservative
estimate that avoids over- or under-stating capital gains.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from engine.models import InvestmentAccount, ACCOUNT_TAX_TREATMENT


# ---------------------------------------------------------------------------
# WithdrawalResult
# ---------------------------------------------------------------------------
@dataclass
class WithdrawalResult:
    """
    Outcome of a single withdrawal operation.

    Fields
    ------
    requested          : amount the projection engine asked to withdraw
    withdrawn          : amount actually taken (≤ requested; limited by balance)
    shortfall          : requested − withdrawn  (0 unless account ran dry)
    ordinary_income    : portion taxable as ordinary income
                         (full amount for tax-deferred; 0 for tax-free/taxable)
    capital_gain       : realised LTCG for brokerage/savings withdrawals
                         (0 for tax-deferred and tax-free accounts)
    basis_used         : cost basis consumed by this withdrawal
    account_name       : name of the source account (for attribution)
    account_type       : e.g. "401k", "brokerage"
    tax_treatment      : "tax_deferred", "tax_free", or "taxable"
    """
    requested:       float
    withdrawn:       float
    shortfall:       float
    ordinary_income: float
    capital_gain:    float
    basis_used:      float
    account_name:    str
    account_type:    str
    tax_treatment:   str


# ---------------------------------------------------------------------------
# AccountState
# ---------------------------------------------------------------------------
@dataclass
class AccountState:
    """
    Mutable simulation state for one investment account.

    Instantiate via ``AccountState.from_account(acct)`` rather than directly.
    The underlying ``InvestmentAccount`` config is read-only; all mutable
    values live here.
    """

    # Reference to original (immutable) config
    account: InvestmentAccount

    # Mutable simulation values
    balance:    float
    cost_basis: float          # tracked principal for brokerage; informational for others

    # Cumulative totals (useful for reporting)
    total_contributed:   float = 0.0
    total_withdrawn:     float = 0.0
    total_growth:        float = 0.0   # cumulative investment gain (balance growth only)
    total_capital_gains: float = 0.0   # realised LTCG from withdrawals

    # ------------------------------------------------------------------ #
    # Factory                                                             #
    # ------------------------------------------------------------------ #
    @classmethod
    def from_account(cls, acct: InvestmentAccount) -> "AccountState":
        """
        Create an AccountState from an InvestmentAccount dataclass.

        If ``acct.cost_basis`` is None, initialises at 50 % of balance.
        """
        cost_basis = acct.cost_basis if acct.cost_basis is not None else acct.balance * 0.50
        return cls(
            account=acct,
            balance=acct.balance,
            cost_basis=cost_basis,
        )

    # ------------------------------------------------------------------ #
    # Convenience properties                                              #
    # ------------------------------------------------------------------ #
    @property
    def name(self) -> str:
        return self.account.name

    @property
    def account_type(self) -> str:
        return self.account.account_type

    @property
    def tax_treatment(self) -> str:
        return self.account.tax_treatment

    @property
    def unrealised_gain(self) -> float:
        """Current unrealised capital gain (balance − cost_basis), floored at 0."""
        return max(0.0, self.balance - self.cost_basis)

    @property
    def gain_fraction(self) -> float:
        """
        Fraction of current balance that represents unrealised gain.
        Used for proportional cost-basis depletion on withdrawal.
        Returns 0 if balance is 0 or basis ≥ balance.
        """
        if self.balance <= 0:
            return 0.0
        return max(0.0, min(1.0, (self.balance - self.cost_basis) / self.balance))

    # ------------------------------------------------------------------ #
    # Monthly growth                                                      #
    # ------------------------------------------------------------------ #
    def grow(self, monthly_rate: Optional[float] = None) -> float:
        """
        Apply one month of compound growth to the balance.

        Parameters
        ----------
        monthly_rate : override the account's own monthly return rate
                       (used by the projection engine when applying a
                       shared scenario return assumption)

        Returns
        -------
        Dollar amount of growth applied this month.
        """
        rate = monthly_rate if monthly_rate is not None else self.account.monthly_return_rate
        growth = self.balance * rate
        self.balance    += growth
        self.total_growth += growth
        # Note: growth does *not* increase cost_basis (it is unrealised gain)
        return growth

    # ------------------------------------------------------------------ #
    # Contributions                                                       #
    # ------------------------------------------------------------------ #
    def contribute(
        self,
        monthly_amount: Optional[float] = None,
        include_match:  bool = True,
    ) -> float:
        """
        Deposit one month's contribution (and optional employer match).

        Parameters
        ----------
        monthly_amount : override the account's own monthly contribution
                         (annual_contribution / 12).  Pass 0 to skip.
        include_match  : if True, also add (employer_match / 12)

        Returns
        -------
        Total amount deposited this month.
        """
        contrib  = (monthly_amount if monthly_amount is not None
                    else self.account.annual_contribution / 12)
        match    = (self.account.employer_match / 12) if include_match else 0.0
        total    = contrib + match

        self.balance          += total
        self.cost_basis       += total   # contributions always increase basis
        self.total_contributed += total
        return total

    # ------------------------------------------------------------------ #
    # Withdrawals                                                         #
    # ------------------------------------------------------------------ #
    def withdraw(self, amount: float) -> WithdrawalResult:
        """
        Execute a withdrawal from this account.

        The withdrawal is capped at the current balance.  Cost basis is
        depleted proportionally (for brokerage/savings accounts).

        Parameters
        ----------
        amount : gross dollar amount to withdraw

        Returns
        -------
        WithdrawalResult with tax-attribution breakdown.
        """
        amount = max(0.0, amount)
        actual = min(amount, self.balance)
        shortfall = amount - actual

        # --- Cost-basis attribution ---
        if self.tax_treatment == "taxable" and actual > 0:
            # Proportional basis depletion
            gain_frac  = self.gain_fraction
            cap_gain   = actual * gain_frac
            basis_used = actual - cap_gain
            ordinary   = 0.0          # taxable accounts: no ordinary income component

            self.cost_basis = max(0.0, self.cost_basis - basis_used)
            self.total_capital_gains += cap_gain

        elif self.tax_treatment == "tax_deferred" and actual > 0:
            # Entire withdrawal is ordinary income; basis not tracked for tax purposes
            cap_gain   = 0.0
            basis_used = 0.0
            ordinary   = actual

        else:
            # tax_free (Roth, HSA): no tax on withdrawal
            cap_gain   = 0.0
            basis_used = 0.0
            ordinary   = 0.0

        self.balance        -= actual
        self.total_withdrawn += actual

        return WithdrawalResult(
            requested=amount,
            withdrawn=actual,
            shortfall=shortfall,
            ordinary_income=ordinary,
            capital_gain=cap_gain,
            basis_used=basis_used,
            account_name=self.name,
            account_type=self.account_type,
            tax_treatment=self.tax_treatment,
        )

    # ------------------------------------------------------------------ #
    # RMD                                                                 #
    # ------------------------------------------------------------------ #
    def rmd_withdrawal(self, rmd_amount: float) -> WithdrawalResult:
        """
        Execute a Required Minimum Distribution withdrawal.

        Functionally identical to a regular withdrawal; kept as a separate
        method so the caller can distinguish RMDs in reporting.
        RMDs only apply to tax-deferred accounts, but the method is safe to
        call on any account (will simply withdraw the requested amount).
        """
        return self.withdraw(rmd_amount)

    # ------------------------------------------------------------------ #
    # Deposit (for RMD overflow → brokerage)                              #
    # ------------------------------------------------------------------ #
    def deposit(self, amount: float) -> None:
        """
        Deposit an arbitrary amount (e.g. excess RMD overflow into brokerage).
        Increases both balance and cost_basis (cash deposit, no gain yet).
        """
        amount = max(0.0, amount)
        self.balance    += amount
        self.cost_basis += amount
        self.total_contributed += amount

    # ------------------------------------------------------------------ #
    # Snapshot for reporting                                              #
    # ------------------------------------------------------------------ #
    def snapshot(self) -> dict:
        """Return a dict of current state values for DataFrame row assembly."""
        return {
            "name":              self.name,
            "account_type":      self.account_type,
            "tax_treatment":     self.tax_treatment,
            "balance":           round(self.balance, 2),
            "cost_basis":        round(self.cost_basis, 2),
            "unrealised_gain":   round(self.unrealised_gain, 2),
            "gain_fraction_pct": round(self.gain_fraction * 100, 2),
            "total_contributed": round(self.total_contributed, 2),
            "total_withdrawn":   round(self.total_withdrawn, 2),
            "total_growth":      round(self.total_growth, 2),
        }


# ---------------------------------------------------------------------------
# Portfolio-level helpers
# ---------------------------------------------------------------------------

def build_portfolio(accounts: list[InvestmentAccount]) -> list[AccountState]:
    """
    Initialise a list of AccountState objects from the user's InvestmentAccount list.
    Preserves the original ordering (important for withdrawal priority logic).
    """
    return [AccountState.from_account(a) for a in accounts]


def total_balance(portfolio: list[AccountState]) -> float:
    """Sum of all account balances."""
    return sum(s.balance for s in portfolio)


def total_by_tax_treatment(portfolio: list[AccountState]) -> dict[str, float]:
    """
    Returns balances grouped by tax treatment.
    Keys: 'taxable', 'tax_deferred', 'tax_free'
    """
    result: dict[str, float] = {"taxable": 0.0, "tax_deferred": 0.0, "tax_free": 0.0}
    for s in portfolio:
        result[s.tax_treatment] = result.get(s.tax_treatment, 0.0) + s.balance
    return result


def grow_all(portfolio: list[AccountState]) -> float:
    """
    Apply one month of growth to every account using its own return rate.
    Returns total portfolio growth for the month.
    """
    return sum(s.grow() for s in portfolio)


def contribute_all(
    portfolio: list[AccountState],
    is_working: dict[str, bool],
) -> float:
    """
    Apply monthly contributions to accounts whose owner is still working.

    Parameters
    ----------
    portfolio  : list of AccountState
    is_working : dict mapping owner key ("self" or "spouse") → bool

    Returns
    -------
    Total amount contributed across all accounts this month.
    """
    total = 0.0
    for state in portfolio:
        owner = state.account.owner
        if is_working.get(owner, False):
            total += state.contribute(include_match=True)
    return total
