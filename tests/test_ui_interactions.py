import time
import os
from datetime import date
import pytest
from dash.testing.composite import DashComposite
from app import app
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait

# Automatically manage ChromeDriver installation and pathing
try:
    from selenium import webdriver
    from webdriver_manager.chrome import ChromeDriverManager
    from selenium.webdriver.chrome.service import Service as ChromeService

    _driver_path = ChromeDriverManager().install()
    _driver_dir = os.path.dirname(_driver_path)
    if _driver_dir not in os.environ["PATH"]:
        os.environ["PATH"] += os.pathsep + _driver_dir
except ImportError:
    pass

from selenium.webdriver.common.keys import Keys

@pytest.fixture
def dash_duo(dash_duo) -> DashComposite:
    dash_duo.driver.maximize_window()
    dash_duo.start_server(app)
    return dash_duo

def test_001_tab_switching(dash_duo):
    """Verify that we can navigate between all primary tabs without crashing."""
    tabs = [
        ("nav-profile", "Profile & Settings"),
        ("nav-income", "Income Sources"),
        ("nav-expenses", "Expenses"),
        ("nav-investments", "Investments"),
        ("nav-real-estate", "Real Estate"),
        ("nav-projections", "Projections"),
        ("nav-dashboard", "Dashboard")
    ]

    for nav_id, title_part in tabs:
        # Click via standard click first; fail-over to JS click if needed
        try:
            dash_duo.find_element(f"#{nav_id}").click()
        except:
            el = dash_duo.find_element(f"#{nav_id}")
            dash_duo.driver.execute_script("arguments[0].click();", el)

        # Wait for title to contain part of the string (robust against emoji/spacing)
        WebDriverWait(dash_duo.driver, 10).until(
            lambda d: title_part in d.find_element(By.ID, "topbar-title").text
        )
        assert dash_duo.get_logs() == [], f"Console errors detected on {title_part}"

@pytest.mark.parametrize("p_type", ["self", "spouse"])
def test_002_comprehensive_person_profile_sync(dash_duo, p_type):
    """Rigorous exhaustive test for all primary person profile fields (Self and Spouse)."""
    dash_duo.find_element("#nav-profile").click()

    _cur_year = date.today().year

    # 1. Configuration for the specific person type
    # We use different values to ensure they don't leak between people
    data = {
        "self":   {"name": "Testing Self",   "birth_year": str(_cur_year - 40), "birth_month": "1", "retirement_year": str(_cur_year - 40 + 60), "ss": "4000", "y_ret": "20", "ss_str": "$4,000/mo"},
        "spouse": {"name": "Testing Spouse", "birth_year": str(_cur_year - 55), "birth_month": "1", "retirement_year": str(_cur_year - 55 + 65), "ss": "3000", "y_ret": "10", "ss_str": "$3,000/mo"}
    }[p_type]

    # 2. Update all fields
    # Name
    name_in = dash_duo.find_element(f"#profile-{p_type}-name")
    name_in.send_keys(Keys.CONTROL + "a")
    name_in.send_keys(Keys.BACKSPACE)
    name_in.send_keys(data["name"])

    # Helper: scroll element into view then send keys
    def _scroll_and_send(el, text):
        dash_duo.driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", el)
        time.sleep(0.3)
        el.send_keys(Keys.CONTROL + "a")
        el.send_keys(Keys.BACKSPACE)
        el.send_keys(text)
        el.send_keys(Keys.TAB)

    # Birth Year
    birth_year_in = dash_duo.find_element(f"#profile-{p_type}-birth-year")
    _scroll_and_send(birth_year_in, data["birth_year"])
    time.sleep(0.5)  # Allow auto-save debounce

    # Birth Month
    birth_month_in = dash_duo.find_element(f"#profile-{p_type}-birth-month")
    _scroll_and_send(birth_month_in, data["birth_month"])
    time.sleep(0.5)

    # Retirement Year
    ret_yr_in = dash_duo.find_element(f"#profile-{p_type}-retirement-year")
    _scroll_and_send(ret_yr_in, data["retirement_year"])
    time.sleep(0.5)

    # SS Benefit
    ss_in = dash_duo.find_element(f"#profile-{p_type}-ss-benefit")
    _scroll_and_send(ss_in, data["ss"])
    time.sleep(0.5)

    # 3. Auto-save is triggered on input changes; wait for store update
    time.sleep(1.0)

    # 4. Verify REAL-TIME SUMMARY UPDATES
    WebDriverWait(dash_duo.driver, 15).until(
        lambda d: (data["y_ret"] in d.find_element(By.CSS_SELECTOR, "#profile-summary-container").text.lower() and
                   data["ss_str"] in d.find_element(By.CSS_SELECTOR, "#profile-summary-container").text)
    )

    # 5. REFRESH & VERIFY PERSISTENCE
    dash_duo.driver.refresh()
    WebDriverWait(dash_duo.driver, 10).until(
        lambda d: d.find_element(By.ID, f"profile-{p_type}-name").get_attribute("value") == data["name"]
    )
    assert dash_duo.find_element(By.ID, f"profile-{p_type}-birth-year").get_attribute("value") == data["birth_year"]
    assert dash_duo.find_element(By.ID, f"profile-{p_type}-birth-month").get_attribute("value") == data["birth_month"]
    assert dash_duo.find_element(By.ID, f"profile-{p_type}-retirement-year").get_attribute("value") == data["retirement_year"]
    assert dash_duo.find_element(By.ID, f"profile-{p_type}-ss-benefit").get_attribute("value") == data["ss"]

def test_003_dynamic_account_management(dash_duo):
    """Test Add -> Edit -> Save -> Remove workflow for dynamic accounts."""
    dash_duo.find_element("#nav-investments").click()

    # Wait for initial load
    time.sleep(1)
    initial_items = len(dash_duo.find_elements(".dynamic-item"))

    # Add Account
    btn_add = dash_duo.find_element("#btn-add-account")
    dash_duo.driver.execute_script("arguments[0].click();", btn_add)

    # Wait for DOM update
    WebDriverWait(dash_duo.driver, 10).until(
        lambda d: len(d.find_elements(By.CLASS_NAME, "dynamic-item")) == initial_items + 1
    )

    # Name the new account
    all_names = dash_duo.find_elements("input[id*='acc-name']")
    all_names[-1].send_keys("Test Account Z")

    # Save
    btn_save = dash_duo.find_element("#investments-save-btn")
    dash_duo.driver.execute_script("arguments[0].click();", btn_save)
    dash_duo.wait_for_text_to_equal(".toast-container .toast-body", "Portfolio structure synced.", timeout=15)

    # REMOVE (Soft Delete)
    all_remove_btns = dash_duo.find_elements("button[id*='btn-delete-account']")
    dash_duo.driver.execute_script("arguments[0].click();", all_remove_btns[-1])

    # Verify visually hidden
    all_wrappers = dash_duo.find_elements("div[id*='account-item']")
    WebDriverWait(dash_duo.driver, 10).until(
        lambda d: all_wrappers[-1].value_of_css_property("display") == "none"
    )

    # Save again
    btn_save = dash_duo.find_element("#investments-save-btn")
    dash_duo.driver.execute_script("arguments[0].click();", btn_save)
    dash_duo.wait_for_text_to_equal(".toast-container .toast-body", "Portfolio structure synced.", timeout=15)

    # Final check: Refresh/Nav back
    dash_duo.find_element("#nav-dashboard").click()
    dash_duo.find_element("#nav-investments").click()

    WebDriverWait(dash_duo.driver, 10).until(
        lambda d: len(d.find_elements(By.CLASS_NAME, "dynamic-item")) == initial_items
    )

def test_004_load_plan_workflow(dash_duo):
    """Verify that uploading a plan JSON file correctly updates the profile store."""
    # Create a small valid test profile JSON
    _cur_year = date.today().year
    test_profile = {
        "plan_name": "Testing Load Workflow",
        "self_person": {"name": "Loaded User", "birth_year": _cur_year - 45, "birth_month": 1}
    }
    import json
    import tempfile

    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        json.dump(test_profile, f)
        temp_path = f.name

    try:
        dash_duo.find_element("#upload-plan input").send_keys(temp_path)

        # Wait for success toast
        dash_duo.wait_for_text_to_equal(".toast-header strong", "Load Success", timeout=10)

        # Verify navigation to dashboard (root)
        dash_duo.wait_for_text_to_equal("#topbar-title", "📊 Dashboard", timeout=5)

        # Verify data reached the profile page
        dash_duo.find_element("#nav-profile").click()
        WebDriverWait(dash_duo.driver, 10).until(
            lambda d: d.find_element(By.ID, "profile-self-name").get_attribute("value") == "Loaded User"
        )
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)
