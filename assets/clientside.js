window.dash_clientside = Object.assign({}, window.dash_clientside, {
    clientside: {
        sync_income_header: function(name, amt) {
            const display_name = name || "New Income Source";
            const display_amt = "$" + (Number(amt) || 0).toLocaleString() + " / yr";
            return [display_name, display_amt];
        },
        sync_expense_header: function(name, amt, cat) {
            const display_name = name || (cat ? cat.charAt(0).toUpperCase() + cat.slice(1) : "Expense");
            const display_amt = "$" + (Number(amt) || 0).toLocaleString() + " / mo";
            return [display_name, display_amt];
        },
        sync_otex_header: function(name, amt, yr) {
            const display_name = name || "New Expense";
            const display_amt = "$" + (Number(amt) || 0).toLocaleString() + " in " + (yr || 2030);
            return [display_name, display_amt];
        },
        sync_account_header: function(name, bal) {
            const display_name = name || "New Account";
            const display_amt = "$" + (Number(bal) || 0).toLocaleString();
            return [display_name, display_amt];
        },
        sync_property_header: function(name, val) {
            const display_name = name || "New Property";
            const display_amt = "Valued at $" + (Number(val) || 0).toLocaleString();
            return [display_name, display_amt];
        }
    }
});
