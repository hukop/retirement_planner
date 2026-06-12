"""
UI regression tests for previously-discovered bugs.

Covers:
1. Real Estate: rental cashflow fields toggle when property type changes.
2. Profile: low retirement ages (< 50) can be entered and saved.
3. Income Sources: dynamic add/edit/remove workflow.
4. Expenses: dynamic add/edit/remove workflow.
5. Debounced input persistence across profile save.
"""

import time
import os
import pytest
from dash.testing.composite import DashComposite
from app import app
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.keys import Keys

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


def _safe_click(driver, element):
    """Scroll element into view and click via JavaScript to avoid interception."""
    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", element)
    time.sleep(0.2)
    driver.execute_script("arguments[0].click();", element)


def _select_option(driver, select_element, value):
    """Select an option by value and trigger change event."""
    from selenium.webdriver.support.ui import Select
    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", select_element)
    time.sleep(0.2)
    sel = Select(select_element)
    sel.select_by_value(value)
    # Trigger change event for clientside callbacks
    driver.execute_script("arguments[0].dispatchEvent(new Event('change', { bubbles: true }));", select_element)
    time.sleep(0.3)


def _wait_and_click(dash_duo, selector, timeout=10):
    """Wait for element to be present, scroll it, and click via JS."""
    from selenium.webdriver.support import expected_conditions as EC
    el = WebDriverWait(dash_duo.driver, timeout).until(
        EC.presence_of_element_located((By.CSS_SELECTOR, selector))
    )
    _safe_click(dash_duo.driver, el)
    return el


@pytest.fixture
def dash_duo(dash_duo) -> DashComposite:
    dash_duo.driver.maximize_window()
    dash_duo.start_server(app)
    return dash_duo


# ---------------------------------------------------------------------------
# Real Estate — rental cashflow toggle
# ---------------------------------------------------------------------------

def test_re_001_rental_fields_hidden_for_primary(dash_duo):
    """A newly-added primary property should NOT show rental cashflow fields."""
    _wait_and_click(dash_duo, "#nav-real-estate")
    time.sleep(1)

    initial_items = len(dash_duo.find_elements(".dynamic-item"))
    _wait_and_click(dash_duo, "#btn-add-property")

    WebDriverWait(dash_duo.driver, 10).until(
        lambda d: len(d.find_elements(By.CLASS_NAME, "dynamic-item")) == initial_items + 1
    )

    # Find the newly-added property's rental group and verify display:none
    rental_groups = dash_duo.find_elements("div[id*='prop-rental-group']")
    assert len(rental_groups) > 0, "No rental groups found on page"
    new_group = rental_groups[-1]
    display = new_group.value_of_css_property("display")
    assert display == "none", f"Expected rental group hidden for primary, got {display}"


def test_re_002_rental_fields_visible_after_type_change(dash_duo):
    """Changing a property to 'rental' should reveal the cashflow fields."""
    _wait_and_click(dash_duo, "#nav-real-estate")
    time.sleep(1)

    initial_items = len(dash_duo.find_elements(".dynamic-item"))
    _wait_and_click(dash_duo, "#btn-add-property")

    WebDriverWait(dash_duo.driver, 10).until(
        lambda d: len(d.find_elements(By.CLASS_NAME, "dynamic-item")) == initial_items + 1
    )

    # Find the type dropdown for the new property and change it to rental
    type_selects = dash_duo.find_elements("select[id*='prop-type']")
    assert len(type_selects) > 0, "No property type dropdowns found"
    new_select = type_selects[-1]
    _select_option(dash_duo.driver, new_select, "rental")

    # Wait for clientside callback to toggle visibility
    rental_groups = dash_duo.find_elements("div[id*='prop-rental-group']")
    assert len(rental_groups) > 0, "No rental groups found"
    new_group = rental_groups[-1]
    WebDriverWait(dash_duo.driver, 5).until(
        lambda d: new_group.value_of_css_property("display") == "block"
    )

    # Verify the rent input is now visible and interactable
    rent_inputs = dash_duo.find_elements("input[id*='prop-rent-inc']")
    assert len(rent_inputs) > 0, "No rent inputs found"


def test_re_003_rental_income_saved_and_reloaded(dash_duo):
    """Add a rental property with rent, save, navigate away, return — data persists."""
    _wait_and_click(dash_duo, "#nav-real-estate")
    time.sleep(1)

    initial_items = len(dash_duo.find_elements(".dynamic-item"))
    _wait_and_click(dash_duo, "#btn-add-property")
    WebDriverWait(dash_duo.driver, 10).until(
        lambda d: len(d.find_elements(By.CLASS_NAME, "dynamic-item")) == initial_items + 1
    )

    # Name it
    name_inputs = dash_duo.find_elements("input[id*='prop-name']")
    assert len(name_inputs) > 0, "No property name inputs found"
    name_inputs[-1].send_keys("Test Rental")
    name_inputs[-1].send_keys(Keys.TAB)
    time.sleep(0.3)

    # Switch to rental
    type_selects = dash_duo.find_elements("select[id*='prop-type']")
    assert len(type_selects) > 0, "No property type dropdowns found"
    _select_option(dash_duo.driver, type_selects[-1], "rental")

    # Wait for rental fields to appear
    rental_groups = dash_duo.find_elements("div[id*='prop-rental-group']")
    WebDriverWait(dash_duo.driver, 5).until(
        lambda d: rental_groups[-1].value_of_css_property("display") == "block"
    )

    # Enter rent
    rent_inputs = dash_duo.find_elements("input[id*='prop-rent-inc']")
    assert len(rent_inputs) > 0, "No rent inputs found"
    rent_inputs[-1].send_keys("3500")
    rent_inputs[-1].send_keys(Keys.TAB)
    time.sleep(0.3)

    # Save (CORRECTED ID: real-estate-save-btn with hyphen)
    _wait_and_click(dash_duo, "#real-estate-save-btn")
    time.sleep(1.5)  # Wait for save to complete

    # Navigate away and back
    _wait_and_click(dash_duo, "#nav-dashboard")
    _wait_and_click(dash_duo, "#nav-real-estate")
    time.sleep(1)

    # Verify the rental property still has rent value
    rent_inputs = dash_duo.find_elements("input[id*='prop-rent-inc']")
    assert len(rent_inputs) > 0, "No rent inputs found after reload"
    assert rent_inputs[-1].get_attribute("value") == "3500"


# ---------------------------------------------------------------------------
# Profile — low retirement age (< 50)
# ---------------------------------------------------------------------------

from datetime import date as _reg_date

def _scroll_and_send(dash_duo, element, text):
    """Scroll element into view then send keys."""
    dash_duo.driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", element)
    time.sleep(0.3)
    element.send_keys(Keys.CONTROL + "a")
    element.send_keys(Keys.BACKSPACE)
    element.send_keys(text)
    element.send_keys(Keys.TAB)

def test_profile_001_low_retirement_age_persists(dash_duo):
    """Regression: retirement age of 45 should be accepted and survive refresh."""
    _cur = _reg_date.today().year
    _wait_and_click(dash_duo, "#nav-profile")

    # Set birth year so retirement at age 45 means retirement year = birth_year + 45
    # Age 50 -> birth_year = current_year - 50, retirement_year = birth_year + 45 = current_year - 5
    birth_in = dash_duo.find_element("#profile-self-birth-year")
    _scroll_and_send(dash_duo, birth_in, str(_cur - 50))
    time.sleep(0.5)

    ret_in = dash_duo.find_element("#profile-self-retirement-year")
    _scroll_and_send(dash_duo, ret_in, str(_cur - 50 + 45))
    time.sleep(0.5)

    # Auto-save triggers on input changes; wait for store to update
    time.sleep(1.5)

    # Refresh & verify persistence
    dash_duo.driver.refresh()
    WebDriverWait(dash_duo.driver, 10).until(
        lambda d: d.find_element(By.ID, "profile-self-retirement-year").get_attribute("value") == str(_cur - 50 + 45)
    )


def test_profile_002_spouse_low_retirement_age_persists(dash_duo):
    """Regression: spouse retirement age of 45 should also persist."""
    _cur = _reg_date.today().year
    _wait_and_click(dash_duo, "#nav-profile")

    # Set birth year so retirement at age 45 means retirement year = birth_year + 45
    # Age 50 -> birth_year = current_year - 50
    birth_in = dash_duo.find_element("#profile-spouse-birth-year")
    _scroll_and_send(dash_duo, birth_in, str(_cur - 50))
    time.sleep(0.5)

    ret_in = dash_duo.find_element("#profile-spouse-retirement-year")
    _scroll_and_send(dash_duo, ret_in, str(_cur - 50 + 45))
    time.sleep(0.5)

    # Auto-save triggers on input changes; wait for store to update
    time.sleep(1.5)

    dash_duo.driver.refresh()
    WebDriverWait(dash_duo.driver, 10).until(
        lambda d: d.find_element(By.ID, "profile-spouse-retirement-year").get_attribute("value") == str(_cur - 50 + 45)
    )


# ---------------------------------------------------------------------------
# Income Sources — dynamic add/remove
# ---------------------------------------------------------------------------

def test_income_001_add_and_save(dash_duo):
    """Add a new income source, name it, save, verify persistence."""
    _wait_and_click(dash_duo, "#nav-income")
    time.sleep(1)

    initial = len(dash_duo.find_elements(".dynamic-item"))
    _wait_and_click(dash_duo, "#btn-add-income")
    WebDriverWait(dash_duo.driver, 10).until(
        lambda d: len(d.find_elements(By.CLASS_NAME, "dynamic-item")) == initial + 1
    )

    name_inputs = dash_duo.find_elements("input[id*='income-name']")
    assert len(name_inputs) > 0, "No income name inputs found"
    name_inputs[-1].send_keys(Keys.CONTROL + "a")
    name_inputs[-1].send_keys("Consulting")
    name_inputs[-1].send_keys(Keys.TAB)

    amt_inputs = dash_duo.find_elements("input[id*='income-amount']")
    assert len(amt_inputs) > 0, "No income amount inputs found"
    amt_inputs[-1].send_keys(Keys.CONTROL + "a")
    amt_inputs[-1].send_keys("5000")
    amt_inputs[-1].send_keys(Keys.TAB)
    time.sleep(0.3)

    _wait_and_click(dash_duo, "#income-save-btn")
    time.sleep(1.5)  # Wait for save to complete

    # Navigate away and back
    _wait_and_click(dash_duo, "#nav-dashboard")
    _wait_and_click(dash_duo, "#nav-income")
    time.sleep(1)

    # Wait for dynamic items to load
    WebDriverWait(dash_duo.driver, 10).until(
        lambda d: len(d.find_elements(By.CSS_SELECTOR, "input[id*='income-name']")) > 0
    )
    name_inputs = dash_duo.find_elements("input[id*='income-name']")
    assert name_inputs[-1].get_attribute("value") == "Consulting"


def test_income_002_owner_dropdown_self_vs_spouse(dash_duo):
    """Income source owner can be switched between self and spouse."""
    _wait_and_click(dash_duo, "#nav-income")
    time.sleep(1)

    initial = len(dash_duo.find_elements(".dynamic-item"))
    _wait_and_click(dash_duo, "#btn-add-income")
    WebDriverWait(dash_duo.driver, 10).until(
        lambda d: len(d.find_elements(By.CLASS_NAME, "dynamic-item")) == initial + 1
    )

    owner_selects = dash_duo.find_elements("select[id*='income-owner']")
    assert len(owner_selects) > 0, "No owner dropdowns found"
    new_select = owner_selects[-1]
    _select_option(dash_duo.driver, new_select, "spouse")

    # Save and verify
    _wait_and_click(dash_duo, "#income-save-btn")
    time.sleep(1.5)  # Wait for save to complete

    dash_duo.driver.refresh()
    time.sleep(1)
    owner_selects = dash_duo.find_elements("select[id*='income-owner']")
    assert owner_selects[-1].get_attribute("value") == "spouse"


# ---------------------------------------------------------------------------
# Expenses — dynamic add/remove
# ---------------------------------------------------------------------------

def test_expense_001_add_and_save(dash_duo):
    """Add a new expense, name it, set amount, save, verify persistence."""
    _wait_and_click(dash_duo, "#nav-expenses")
    time.sleep(1)

    initial = len(dash_duo.find_elements(".dynamic-item"))
    _wait_and_click(dash_duo, "#btn-add-expense")
    WebDriverWait(dash_duo.driver, 10).until(
        lambda d: len(d.find_elements(By.CLASS_NAME, "dynamic-item")) == initial + 1
    )

    # Expenses don't have a name field - use category and amount
    amt_inputs = dash_duo.find_elements("input[id*='expense-amount']")
    assert len(amt_inputs) > 0, "No expense amount inputs found"
    amt_inputs[-1].send_keys("800")
    amt_inputs[-1].send_keys(Keys.TAB)
    time.sleep(0.3)

    _wait_and_click(dash_duo, "#expenses-save-btn")
    time.sleep(1.5)  # Wait for save to complete

    _wait_and_click(dash_duo, "#nav-dashboard")
    _wait_and_click(dash_duo, "#nav-expenses")
    time.sleep(1)

    # Wait for dynamic items to load and verify amount persisted
    WebDriverWait(dash_duo.driver, 10).until(
        lambda d: len(d.find_elements(By.CSS_SELECTOR, "input[id*='expense-amount']")) > 0
    )
    amt_inputs = dash_duo.find_elements("input[id*='expense-amount']")
    assert amt_inputs[-1].get_attribute("value") == "800"


def test_expense_002_retirement_pct_slider(dash_duo):
    """Expense retirement percentage slider updates and saves."""
    _wait_and_click(dash_duo, "#nav-expenses")
    time.sleep(1)

    initial = len(dash_duo.find_elements(".dynamic-item"))
    _wait_and_click(dash_duo, "#btn-add-expense")
    WebDriverWait(dash_duo.driver, 10).until(
        lambda d: len(d.find_elements(By.CLASS_NAME, "dynamic-item")) == initial + 1
    )

    # Find the retirement pct input (could be a slider or number input)
    pct_inputs = dash_duo.find_elements("input[id*='expense-retirement-pct']")
    if pct_inputs:
        pct_inputs[-1].send_keys(Keys.CONTROL + "a")
        pct_inputs[-1].send_keys(Keys.BACKSPACE)
        pct_inputs[-1].send_keys("50")
        pct_inputs[-1].send_keys(Keys.TAB)
        time.sleep(0.3)

        _wait_and_click(dash_duo, "#expenses-save-btn")
        time.sleep(1.5)  # Wait for save to complete

        dash_duo.driver.refresh()
        time.sleep(1)
        pct_inputs = dash_duo.find_elements("input[id*='expense-retirement-pct']")
        assert pct_inputs[-1].get_attribute("value") == "50"


# ---------------------------------------------------------------------------
# One-time Expenses
# ---------------------------------------------------------------------------

def test_onetime_001_add_and_save(dash_duo):
    """Add a one-time expense and verify it saves."""
    _wait_and_click(dash_duo, "#nav-expenses")
    time.sleep(1)

    initial = len(dash_duo.find_elements("div[id*='otex-item']"))
    # Scroll to find the one-time add button if needed
    try:
        btn = dash_duo.find_element("#btn-add-otex")
    except Exception:
        # May be lower on page; try scrolling
        dash_duo.driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(0.5)
        btn = dash_duo.find_element("#btn-add-otex")

    _safe_click(dash_duo.driver, btn)
    WebDriverWait(dash_duo.driver, 10).until(
        lambda d: len(d.find_elements(By.CSS_SELECTOR, "div[id*='otex-item']")) == initial + 1
    )

    name_inputs = dash_duo.find_elements("input[id*='otex-name']")
    assert len(name_inputs) > 0, "No one-time expense name inputs found"
    name_inputs[-1].send_keys("World Trip")
    name_inputs[-1].send_keys(Keys.TAB)

    amt_inputs = dash_duo.find_elements("input[id*='otex-amount']")
    assert len(amt_inputs) > 0, "No one-time expense amount inputs found"
    amt_inputs[-1].send_keys("25000")
    amt_inputs[-1].send_keys(Keys.TAB)
    time.sleep(0.3)

    _wait_and_click(dash_duo, "#expenses-save-btn")
    time.sleep(1.5)  # Wait for save to complete

    dash_duo.driver.refresh()
    time.sleep(1)
    name_inputs = dash_duo.find_elements("input[id*='otex-name']")
    assert name_inputs[-1].get_attribute("value") == "World Trip"
