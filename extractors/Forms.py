from selenium import webdriver
from selenium.webdriver.support.select import Select
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.common.action_chains import ActionChains
from selenium.common.exceptions import StaleElementReferenceException, TimeoutException, \
    UnexpectedAlertPresentException, NoSuchFrameException, NoAlertPresentException, ElementNotVisibleException, \
    InvalidElementStateException
from urllib.parse import urlparse, urljoin
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
import json
import pprint
import datetime
import tldextract
import math
import os
import traceback
import random
import re
import logging
import copy
import time

import Classes

from tools import get_accessible_name


def get_element_text(driver, element):
    return driver.execute_script("return arguments[0].textContent;", element).strip()

def extract_dom_context(el, driver):
    dom_context = {
        "current_node": {
            "tag_name": el.tag_name,
            "attributes": el.get_attribute('outerHTML'),
            "text": get_element_text(driver, el),
        },
        "parent_node": {},
        "sibling_nodes": [],
        "page_title": driver.title
    }

    try:
        parent = el.find_element(By.XPATH, '..')
        dom_context["parent_node"] = {
            "tag_name": parent.tag_name,
            "attributes": parent.get_attribute('outerHTML'),
            "text": get_element_text(driver, parent)
        }
    except:
        dom_context["parent_node"] = {}

    try:
        siblings = el.find_elements(By.XPATH, '../*')
        siblings = siblings[:10]
        for sibling in siblings:
            if sibling != el:
                dom_context["sibling_nodes"].append({
                    "tag_name": sibling.tag_name,
                    "attributes": sibling.get_attribute('outerHTML'),
                    "text": get_element_text(driver, sibling)
                })
    except:
        dom_context["sibling_nodes"] = []
    return dom_context

def parse_form(el, driver):
    form = Classes.Form()

    form.html = el.get_attribute("outerHTML")

    try:
        if el.get_attribute("action"):
            form.action = el.get_attribute("action")
            if el.get_attribute("method"):
                form.method = el.get_attribute("method")
            else:
                form.method = "get"

    except StaleElementReferenceException as e:
        logging.error("Stale pasta in from action")
        # logging.error(traceback.format_exc())
    except:
        logging.error("Failed to write element")
        # logging.error(traceback.format_exc())

    # <input> tags
    try:
        inputs = el.find_elements(By.TAG_NAME, "input")
    except StaleElementReferenceException as e:
        print("Stale pasta in inputs")
        logging.error("Stale pasta in inputs")
        inputs = None
    except:
        logging.error("Unknown exception in inputs")
        inputs = None

    if not inputs:
        # TODO Exapnd JavaScript for all types of elements
        inputs = []
        logging.warning("No inputs founds during parse, falling back to JavaScript")
        resps = driver.execute_script("return get_forms()")
        js_forms = json.loads(resps)
        for js_form in js_forms:
            current_form = Classes.Form()
            current_form.method = js_form['method']
            current_form.action = js_form['action']
            logging.info("Found js form: " + str(current_form) )

            if current_form.method == form.method and current_form.action == form.action:
                for js_el in js_form['elements']:
                    web_el = driver.find_element(By.XPATH, js_el['xpath'])
                    inputs.append(web_el)
                break

    for iel in inputs:
        # accessible_name
        tmp = {'type': None, 'accessible_name': None, 'name': None, 'value': None, 'checked': None}
        try:
            accessible_name = get_accessible_name(driver, iel)
            if iel.get_attribute("type"):
                tmp['type'] = iel.get_attribute("type")
            if accessible_name:
                tmp['accessible_name'] = accessible_name
            if iel.get_attribute("name"):
                tmp['name'] = iel.get_attribute("name")
            if iel.get_attribute("value"):
                tmp['value'] = iel.get_attribute("value")
            if iel.get_attribute("checked"):
                tmp['checked'] = True
            if iel.aria_role and iel.aria_role == "combobox":
                try:
                    iel.click()
                    time.sleep(0.1)
                    driver.switch_to.active_element.send_keys(Keys.ENTER)
                    tmp['value'] = iel.get_attribute("value")
                except Exception as e:
                    logging.warning(f"Failed to extract value from combobox: {str(e)}")

        except StaleElementReferenceException as e:
            print("Stale pasta in from action")
        except:
            print("Failed to write element")
            print(traceback.format_exc())
        form.add_input(tmp['type'], tmp['accessible_name'], tmp['name'], tmp['value'], tmp['checked'])

    # <select> and <option> tags
    selects = el.find_elements(By.TAG_NAME, "select")
    for select in selects:
        tmp = {'accessible_name': None, 'name': None, 'value': None}
        accessible_name = get_accessible_name(driver, select)
        if accessible_name:
            tmp['accessible_name'] = accessible_name
        if select.get_attribute("name"):
            tmp['name'] = select.get_attribute("name")
        if select.get_attribute("value"):
            tmp['value'] = select.get_attribute("value")
        form_select = form.add_select("select", tmp['accessible_name'], tmp['name'], tmp['value'])

        selenium_select = Select( select )
        options = selenium_select.options
        for option in options:
            text = get_element_text(driver, option)
            form_select.add_option( option.get_attribute("value"), text)

    # <textarea> tags
    textareas = el.find_elements(By.TAG_NAME, "textarea")
    for ta in textareas:
        tmp = {'accessible_name': None, 'name': None, 'value': None}
        try:
            accessible_name = get_accessible_name(driver, ta)
            if accessible_name:
                tmp['accessible_name'] = accessible_name
            if ta.get_attribute("name"):
                tmp['name'] = ta.get_attribute("name")
            if ta.get_attribute("value"):
                tmp['value'] = ta.get_attribute("value")

        except StaleElementReferenceException as e:
            print("Stale pasta in from action")
        except:
            print("Failed to write element")
            print(traceback.format_exc())
        form.add_textarea(tmp['accessible_name'], tmp['name'], tmp['value'])

    # <button> tags
    buttons = el.find_elements(By.TAG_NAME, "button")
    for button in buttons:
        tmp = {'type': None, 'accessible_name': None, 'name': None, 'value': None}
        try:
            accessible_name = get_accessible_name(driver, button)
            if button.get_attribute("type"):
                tmp['type'] = button.get_attribute("type")
            if accessible_name:
                tmp['accessible_name'] = accessible_name
            if button.get_attribute("name"):
                tmp['name'] = button.get_attribute("name")
            if button.get_attribute("value"):
                tmp['value'] = button.get_attribute("value")
        except StaleElementReferenceException as e:
            print("Stale pasta in from action")
        except:
            print("Failed to write element")
            print(traceback.format_exc())
        form.add_button(tmp['type'], tmp['accessible_name'], tmp['name'], tmp['value'])

    a_tags = el.find_elements(By.TAG_NAME, "a")
    for a_tag in a_tags:
        form.add_a_tag(a_tag.get_attribute("id"),
                       get_accessible_name(driver, a_tag),
                       )

    # <iframe> with <body contenteditable>
    iframes = el.find_elements(By.TAG_NAME, "iframe")
    for iframe in iframes:
        iframe_id = iframe.get_attribute("id")
        driver.switch_to.frame(iframe)
        iframe_body = driver.find_element(By.TAG_NAME, "body")

        if iframe_body.get_attribute("contenteditable") == "true":
            accessible_name = get_accessible_name(driver, iframe_body)
            if accessible_name:
                accessible_name = accessible_name
            elif iframe_body.get_attribute("data-id"):
                accessible_name = iframe_body.get_attribute("data-id")
            form.add_iframe_body(iframe_id, accessible_name)

        driver.switch_to.default_content()

    return form


# Search for <form>
def extract_forms(driver):
    form_wait_time = float(os.getenv('FORM_WAIT_TIME', '0.5'))
    time.sleep(form_wait_time)
    elem = driver.find_elements(By.TAG_NAME, "form")
    logging.debug("Current URL: " + driver.current_url)

    forms = set()
    form_contexts = {}
    for el in elem:
        if not el.is_displayed():
            if not el.is_displayed():
                form = parse_form(el, driver)
                logging.warning("Form "+str(form)+" is not displayed, skipping")
                continue
        form = parse_form(el, driver)
        if form.inputs == {}:
            logging.warning("Form has no inputs, skipping")
            continue
        forms.add(form)
        form_contexts[form] = {
            "dom_context": extract_dom_context(el, driver),
            "action_url": el.get_attribute("action") or driver.current_url
        }
    return forms, form_contexts


