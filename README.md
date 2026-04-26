# RetirePlan (Retirement Dashboard MVP)

A highly personalized, local-first Python/Dash web application built to simulate your specific financial retirement roadmap.

## Summary

This application processes detailed custom dataclasses defining standard ordinary income, SS Claiming ages, Investment portfolios (separated by strict Tax characteristics like Pre-Tax, Tax-Free, Capital Gains bounds) and Real Estate Equity configurations mapping explicitly out to your expected lifespans.

## Features

- **Progressive Tax Engines:** Natively maps your nominal capital distributions locally through precise Federal Income Brackets + CA specific multipliers estimating real Tax burdens.
- **SS Actuarial Adjustments:** Calculates and graphs the specific bonus/penalties scaling dynamically off your explicit birth year & structural FRA boundaries.
- **Sequential Drawdowns:** Intelligently zeroes RMD burdens and draws down capital according to Taxable -> Tax-Deferred -> Roth hierarchies.
- **Dark-Themed UI:** Glassmorphic modern interface engineered explicitly for visual tracking via Plotly.
- **Local Persistence:** All data isolates strictly via local `data/profiles/my_plan.json` serialization logic.

## Usage

You can launch the dashboard using the integrated script:

```bash
launch.bat
```

Or run manually:

```bash
python app.py
```

Then navigate to `http://127.0.0.1:8050` in your browser.

## Testing

A custom unittesting integration suite is located in `/tests`. Execute it via:

```bash
python -m unittest discover tests
```
