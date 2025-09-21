from lib2to3.fixes.fix_input import context

from selenium import webdriver
from selenium.webdriver.support.select import Select
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.common.action_chains import ActionChains
from selenium.common.exceptions import StaleElementReferenceException, TimeoutException, UnexpectedAlertPresentException, NoSuchFrameException, NoAlertPresentException, ElementNotVisibleException, InvalidElementStateException
from urllib.parse import urlparse, urljoin
from selenium.webdriver.common.by import By
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
import html2text

import Classes

def get_element_text(driver, element):
    return driver.execute_script("return arguments[0].textContent;", element).strip()

def extract_dom_context_for_iframe(iframe_element, driver):
    dom_context = {
        "current_node": {
            "tag_name": iframe_element.tag_name,
            "attributes": iframe_element.get_attribute('outerHTML'),
            "text": get_element_text(driver, iframe_element),
        },
        "parent_node": {},
        "sibling_nodes": [],
        "page_title": driver.title
    }

    try:
        parent = iframe_element.find_element(By.XPATH, '..')
        dom_context["parent_node"] = {
            "tag_name": parent.tag_name,
            "attributes": parent.get_attribute('outerHTML'),
            "text": get_element_text(driver, parent)
        }
    except:
        dom_context["parent_node"] = {}

    try:
        siblings = iframe_element.find_elements(By.XPATH, '../*')
        siblings = siblings[:10]
        for sibling in siblings:
            if sibling != iframe_element:
                dom_context["sibling_nodes"].append({
                    "tag_name": sibling.tag_name,
                    "attributes": sibling.get_attribute('outerHTML'),
                    "text": get_element_text(driver, sibling)
                })
    except:
        dom_context["sibling_nodes"] = []

    iframe_content = ""

    try:
        driver.switch_to.frame(iframe_element)
        text_maker = html2text.HTML2Text()
        text_maker.ignore_links = True
        iframe_content = text_maker.handle(driver.page_source)
    except Exception as e:
        logging.warning(f"Failed to extract content from iframe: {str(e)}")
    finally:
        driver.switch_to.default_content()

    url = driver.current_url
    context = {
        "dom_context": dom_context,
        "iframe_content": iframe_content,
        "url": url
    }
    return context

def extract_iframes(driver):
    # Search for <iframe>
    iframes = set()
    iframe_contexts = {}
    elem = driver.find_elements(By.TAG_NAME, "iframe")
    for el in elem:
        try:
            src = None
            i = None

            if el.get_attribute("src"):
                src = el.get_attribute("src")
            if el.get_attribute("id"):
                i = el.get_attribute("id")

            iframe = Classes.Iframe(i, src)
            iframes.add(iframe)
            iframe_contexts[iframe] = extract_dom_context_for_iframe(el, driver)

        except StaleElementReferenceException as e:
            print("Stale pasta in from action")
        except:
            print("Failed to write element")
            print(traceback.format_exc())


    # Search for <frame>
    elem = driver.find_elements(By.TAG_NAME, "frame")
    for el in elem:
        try:
            src = None
            i = None

            if el.get_attribute("src"):
                src = el.get_attribute("src")
            if el.get_attribute("id"):
                i = el.get_attribute("i")

            iframe = Classes.Iframe(i, src)
            iframes.add(iframe)
            iframe_contexts[iframe] = extract_dom_context_for_iframe(el, driver)

        except StaleElementReferenceException as e:
            print("Stale pasta in from action")
        except:
            print("Failed to write element")
            print(traceback.format_exc())

    
    return iframes, iframe_contexts
 
