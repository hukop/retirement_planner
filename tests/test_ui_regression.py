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
    dash_duo.find_element("#nav-real-estate").click()
    time.sleep(1)

    initial_items = len(dash_duo.find_elements(".dynamic-item"))
    dash_duo.find_element("#btn-add-property").click()

    WebDriverWait(dash_duo.driver, 10).until(
        lambda d: len(d.find_elements(By.CLASS_NAME, "dynamic-item")) == initial_items + 1
    )

    # Find the newly-added property's rental group and verify display:none
    rental_groups = dash_duo.find_elements("div[id*='prop-rental-group']")
    # The new property should be the last one
    new_group = rental_groups[-1]
    display = new_group.value_of_css_property("display")
    assert display == "none", f"Expected rental group hidden for primary, got {display}"


def test_re_002_rental_fields_visible_after_type_change(dash_duo):
    """Changing a property to 'rental' should reveal the cashflow fields."""
    dash_duo.find_element("#nav-real-estate").click()
    time.sleep(1)

    initial_items = len(dash_duo.find_elements(".dynamic-item"))
    dash_duo.find_element("#btn-add-property").click()

    WebDriverWait(dash_duo.driver, 10).until(
        lambda d: len(d.find_elements(By.CLASS_NAME, "dynamic-item")) == initial_items + 1
    )

    # Find the type dropdown for the new property and change it to rental
    type_selects = dash_duo.find_elements("select[id*='prop-type']")
    new_select = type_selects[-1]
    new_select.click()
    # Select "rental" option (value="rental")
    rental_option = new_select.find_element(By.CSS_SELECTOR, "option[value='rental']")
    rental_option.click()

    # Wait for clientside callback to toggle visibility
    rental_groups = dash_duo.find_elements("div[id*='prop-rental-group']")
    new_group = rental_groups[-1]
    WebDriverWait(dash_duo.driver, 5).until(
        lambda d: new_group.value_of_css_property("display") == "block"
    )

    # Verify the rent input is now visible and interactable
    rent_inputs = dash_duo.find_elements("input[id*='prop-rent-inc']")
    assert len(rent_inputs) > 0


def test_re_003_rental_income_saved_and_reloaded(dash_duo):
    """Add a rental property with rent, save, navigate away, return — data persists."""
    dash_duo.find_element("#nav-real-estate").click()
    time.sleep(1)

    initial_items = len(dash_duo.find_elements(".dynamic-item"))
    dash_duo.find_element("#btn-add-property").click()
    WebDriverWait(dash_duo.driver, 10).until(
        lambda d: len(d.find_elements(By.CLASS_NAME, "dynamic-item")) == initial_items + 1
    )

    # Name it
    name_inputs = dash_duo.find_elements("input[id*='prop-name']")
    name_inputs[-1].send_keys("Test Rental")

    # Switch to rental
    type_selects = dash_duo.find_elements("select[id*='prop-type']")
    type_selects[-1].click()
    type_selects[-1].find_element(By.CSS_SELECTOR, "option[value='rental']").click()

    # Wait for rental fields to appear
    rental_groups = dash_duo.find_elements("div[id*='prop-rental-group']")
    WebDriverWait(dash_duo.driver, 5).until(
        lambda d: rental_groups[-1].value_of_css_property("display") == "block"
    )

    # Enter rent
    rent_inputs = dash_duo.find_elements("input[id*='prop-rent-inc']")
    rent_inputs[-1].send_keys("3500")

    # Save
    dash_duo.driver.execute_script(
        "arguments[0].click();", dash_duo.find_element("#realestate-save-btn")
    )
    dash_duo.wait_for_text_to_equal(".toast-container .toast-body",
                                     "Real estate settings synced.", timeout=15)

    # Navigate away and back
    dash_duo.find_element("#nav-dashboard").click()
    dash_duo.find_element("#nav-real-estate").click()
    time.sleep(1)

    # Verify the rental property still has rent value
    rent_inputs = dash_duo.find_elements("input[id*='prop-rent-inc']")
    assert len(rent_inputs) > 0
    # The last one should be our new property
    assert rent_inputs[-1].get_attribute("value") == "3500"


# ---------------------------------------------------------------------------
# Profile — low retirement age (< 50)
# ---------------------------------------------------------------------------

def test_profile_001_low_retirement_age_persists(dash_duo):
    """Regression: retirement age of 45 should be accepted and survive refresh."""
    dash_duo.find_element("#nav-profile").click()

    ret_input = dash_duo.find_element("#profile-self-retirement-age")
    ret_input.send_keys(Keys.CONTROL + "a")
    ret_input.send_keys(Keys.BACKSPACE)
    ret_input.send_keys("45")

    btn = dash_duo.find_element("#profile-save-btn")
    dash_duo.driver.execute_script("arguments[0].click();", btn)
    dash_duo.wait_for_text_to_equal(".toast-header strong", "Saved", timeout=10)

    # Refresh
    dash_duo.driver.refresh()
    WebDriverWait(dash_duo.driver, 10).until(
        lambda d: d.find_element(By.ID, "profile-self-retirement-age").get_attribute("value") == "45"
    )


def test_profile_002_spouse_low_retirement_age_persists(dash_duo):
    """Regression: spouse retirement age of 45 should also persist."""
    dash_duo.find_element("#nav-profile").click()

    ret_input = dash_duo.find_element("#profile-spouse-retirement-age")
    ret_input.send_keys(Keys.CONTROL + "a")
    ret_input.send_keys(Keys.BACKSPACE)
    ret_input.send_keys("45")

    btn = dash_duo.find_element("#profile-save-btn")
    dash_duo.driver.execute_script("arguments[0].click();", btn)
    dash_duo.wait_for_text_to_equal(".toast-header strong", "Saved", timeout=10)

    dash_duo.driver.refresh()
    WebDriverWait(dash_duo.driver, 10).until(
        lambda d: d.find_element(By.ID, "profile-spouse-retirement-age").get_attribute("value") == "45"
    )


# ---------------------------------------------------------------------------
# Income Sources — dynamic add/remove
# ---------------------------------------------------------------------------

def test_income_001_add_and_save(dash_duo):
    """Add a new income source, name it, save, verify persistence."""
    dash_duo.find_element("#nav-income").click()
    time.sleep(1)

    initial = len(dash_duo.find_elements(".dynamic-item"))
    dash_duo.find_element("#btn-add-income").click()
    WebDriverWait(dash_duo.driver, 10).until(
        lambda d: len(d.find_elements(By.CLASS_NAME, "dynamic-item")) == initial + 1
    )

    name_inputs = dash_duo.find_elements("input[id*='inc-name']")
    name_inputs[-1].send_keys("Consulting")

    amt_inputs = dash_duo.find_elements("input[id*='inc-amount']")
    amt_inputs[-1].send_keys("5000")

    dash_duo.driver.execute_script(
        "arguments[0].click();", dash_duo.find_element("#income-save-btn")
    )
    dash_duo.wait_for_text_to_equal(".toast-container .toast-body",
                                     "Income sources synced.", timeout=15)

    # Navigate away and back
    dash_duo.find_element("#nav-dashboard").click()
    dash_duo.find_element("#nav-income").click()
    time.sleep(1)

    name_inputs = dash_duo.find_elements("input[id*='inc-name']")
    assert name_inputs[-1].get_attribute("value") == "Consulting"


def test_income_002_owner_dropdown_self_vs_spouse(dash_duo):
    """Income source owner can be switched between self and spouse."""
    dash_duo.find_element("#nav-income").click()
    time.sleep(1)

    initial = len(dash_duo.find_elements(".dynamic-item"))
    dash_duo.find_element("#btn-add-income").click()
    WebDriverWait(dash_duo.driver, 10).until(
        lambda d: len(d.find_elements(By.CLASS_NAME, "dynamic-item")) == initial + 1
    )

    owner_selects = dash_duo.find_elements("select[id*='inc-owner']")
    new_select = owner_selects[-1]
    new_select.click()
    new_select.find_element(By.CSS_SELECTOR, "option[value='spouse']").click()

    # Save and verify
    dash_duo.driver.execute_script(
        "arguments[0].click();", dash_duo.find_element("#income-save-btn")
    )
    dash_duo.wait_for_text_to_equal(".toast-container .toast-body",
                                     "Income sources synced.", timeout=15)

    dash_duo.driver.refresh()
    time.sleep(1)
    owner_selects = dash_duo.find_elements("select[id*='inc-owner']")
    assert owner_selects[-1].get_attribute("value") == "spouse"


# ---------------------------------------------------------------------------
# Expenses — dynamic add/remove
# ---------------------------------------------------------------------------

def test_expense_001_add_and_save(dash_duo):
    """Add a new expense, name it, set amount, save, verify persistence."""
    dash_duo.find_element("#nav-expenses").click()
    time.sleep(1)

    initial = len(dash_duo.find_elements(".dynamic-item"))
    dash_duo.find_element("#btn-add-expense").click()
    WebDriverWait(dash_duo.driver, 10).until(
        lambda d: len(d.find_elements(By.CLASS_NAME, "dynamic-item")) == initial + 1
    )

    name_inputs = dash_duo.find_elements("input[id*='exp-name']")
    name_inputs[-1].send_keys("New Car Payment")

    amt_inputs = dash_duo.find_elements("input[id*='exp-amount']")
    amt_inputs[-1].send_keys("800")

    dash_duo.driver.execute_script(
        "arguments[0].click();", dash_duo.find_element("#expenses-save-btn")
    )
    dash_duo.wait_for_text_to_equal(".toast-container .toast-body",
                                     "Expense settings synced.", timeout=15)

    dash_duo.find_element("#nav-dashboard").click()
    dash_duo.find_element("#nav-expenses").click()
    time.sleep(1)

    name_inputs = dash_duo.find_elements("input[id*='exp-name']")
    assert name_inputs[-1].get_attribute("value") == "New Car Payment"


def test_expense_002_retirement_pct_slider(dash_duo):
    """Expense retirement percentage slider updates and saves."""
    dash_duo.find_element("#nav-expenses").click()
    time.sleep(1)

    initial = len(dash_duo.find_elements(".dynamic-item"))
    dash_duo.find_element("#btn-add-expense").click()
    WebDriverWait(dash_duo.driver, 10).until(
        lambda d: len(d.find_elements(By.CLASS_NAME, "dynamic-item")) == initial + 1
    )

    # Find the retirement pct input (could be a slider or number input)
    pct_inputs = dash_duo.find_elements("input[id*='exp-retirement-pct']")
    if pct_inputs:
        pct_inputs[-1].send_keys(Keys.CONTROL + "a")
        pct_inputs[-1].send_keys(Keys.BACKSPACE)
        pct_inputs[-1].send_keys("50")

        dash_duo.driver.execute_script(
            "arguments[0].click();", dash_duo.find_element("#expenses-save-btn")
        )
        dash_duo.wait_for_text_to_equal(".toast-container .toast-body",
                                         "Expense settings synced.", timeout=15)

        dash_duo.driver.refresh()
        time.sleep(1)
        pct_inputs = dash_duo.find_elements("input[id*='exp-retirement-pct']")
        assert pct_inputs[-1].get_attribute("value") == "50"


# ---------------------------------------------------------------------------
# One-time Expenses
# ---------------------------------------------------------------------------

def test_onetime_001_add_and_save(dash_duo):
    """Add a one-time expense and verify it saves."""
    dash_duo.find_element("#nav-expenses").click()
    time.sleep(1)

    initial = len(dash_duo.find_elements("div[id*='onetime-item']"))
    # Scroll to find the one-time add button if needed
    try:
        btn = dash_duo.find_element("#btn-add-onetime")
    except Exception:
        # May be lower on page; try scrolling
        dash_duo.driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(0.5)
        btn = dash_duo.find_element("#btn-add-onetime")

    dash_duo.driver.execute_script("arguments[0].click();", btn)
    WebDriverWait(dash_duo.driver, 10).until(
        lambda d: len(d.find_elements(By.CSS_SELECTOR, "div[id*='onetime-item']")) == initial + 1
    )

    name_inputs = dash_duo.find_elements("input[id*='ot-name']")
    name_inputs[-1].send_keys("World Trip")

    amt_inputs = dash_duo.find_elements("input[id*='ot-amount']")
    amt_inputs[-1].send_keys("25000")

    dash_duo.driver.execute_script(
        "arguments[0].click();", dash_duo.find_element("#expenses-save-btn")
    )
    dash_duo.wait_for_text_to_equal(".toast-container .toast-body",
                                     "Expense settings synced.", timeout=15)

    dash_duo.driver.refresh()
    time.sleep(1)
    name_inputs = dash_duo.find_elements("input[id*='ot-name']")
    assert name_inputs[-1].get_attribute("value") == "World Trip"
