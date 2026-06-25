# Bug Report: Monthly View Granularity Not Implemented

## Summary
The Projections page displays a "View Granularity" toggle with two buttons: "Annual" and "Monthly". However, the "Monthly" button is disabled and non-functional.

## Current Behavior
- Only "Annual" view is selectable
- "Monthly" button is visible but disabled (greyed out)
- Attempting to click Monthly button has no effect

## Code Location
**File:** `ui/pages/projections.py`
**Lines:** ~271-276

```python
dcc.RadioItems(
    id="projections-view-toggle",
    options=[
        {"label": " Annual   ", "value": "annual"},
        {"label": " Monthly", "value": "monthly", "disabled": True}  # ← Disabled here
    ],
    value="annual",
    inline=True,
    style={"color": "var(--text-primary)", "fontSize": "13px"}
)
```

## Root Cause
The Monthly view option is explicitly disabled with `"disabled": True` and includes a comment: `"Disabled until callbacks phase"`

This indicates the UI element is prepared but the backend callbacks and monthly projection data are not yet implemented.

## What Needs Implementation
1. **Monthly projection data** - Ensure projection engine can output monthly-level detail (currently only annual)
2. **Callback handler** - Create callback for `projections-view-toggle` that switches between annual/monthly views
3. **Monthly chart components** - Adapt chart rendering to display monthly granularity
4. **Data passing** - Pass monthly DataFrame to charts instead of annual

## Impact
Users cannot analyze month-by-month cash flows, which limits visibility into:
- Seasonal income/expense patterns
- Monthly withdrawal sequencing
- Precise timing of tax payments
- Month-to-month account balance changes

## Current Workaround
None - users are limited to annual view only.

## Related Code
Check for existing monthly data in:
- `engine/projections.py` - Monthly row data may already be computed
- `ui/callbacks/` - Look for any partial implementation of monthly view callbacks
