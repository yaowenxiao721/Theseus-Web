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

import Classes

def get_element_text(driver, element):
    return driver.execute_script("return arguments[0].textContent;", element).strip()

def extract_dom_context(el, driver):
    dom_context = {
        "current_node": {
            "tag_name": el.tag_name,
            "attributes": el.get_attribute('outerHTML'),
            "text": get_element_text(driver, el),
        },
        "parent_node": None,
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
        dom_context["parent_node"] = None

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


# If the url is from a form then the form method is used
# However, javascript overrides the form method.
def url_to_request(url, form_method=None):
    purl = urlparse(url)

    if form_method:
        method = form_method
    else:
        method = "get"

    if url and url.startswith("javascript:"):
        method = "javascript"
    if isinstance(url, str) and url.endswith("/"):
        url = url.rstrip("/")
    return Classes.Request(url,method)

def add_url_with_context(el, url, element_type, url_contexts, urls, driver):
    if el:
        dom_context = extract_dom_context(el, driver)
    else:
        dom_context = {
            "current_node": None,
            "parent_node": None,
            "sibling_nodes": None,
            "page_title": driver.title
        }
    url_request = url_to_request(url)
    url_contexts[url_request] = {
        "dom_context": dom_context,
        "element_type": element_type
    }
    urls.add(url_request)

# Looks for a and from urls
def extract_urls(driver):
    urls = set()

    url_contexts = {}

    # Search for urls in <a>
    elem = driver.find_elements(By.TAG_NAME, "a")
    for el in elem:
        try:
            href = el.get_attribute("href")
            current_url_js = driver.current_url+"#"
            if href and (href.startswith("javascript:") or href == current_url_js):
                if href == current_url_js:
                    href = "javascript:void(0);"
                if el.get_attribute("id"):
                    id = el.get_attribute("id")
                    href = href+"id:"+id
                elif el.get_attribute("className"):
                    class_name = el.get_attribute("className")
                    href = href+"class_name:"+class_name
                elif el.get_attribute("onclick"):
                    onclick = el.get_attribute("onclick")
                    href = href+"onclick:"+onclick
                text = get_element_text(driver, el)
                href = href+"text:"+text
                logging.debug("found javascript url " + href)
            if href:
                add_url_with_context(el, href, "a", url_contexts, urls, driver)

        except StaleElementReferenceException as e:
            print("Stale pasta in from action")
        except:
            print("Failed to write element")
            print(traceback.format_exc())

    # Search for urls in <iframe>
    elem = driver.find_elements(By.TAG_NAME, "iframe")
    for el in elem:
        try:
            src = el.get_attribute("src")
            if src:
                add_url_with_context(el, src, "iframe", url_contexts, urls, driver)

        except StaleElementReferenceException as e:
            print("Stale pasta in from action")
        except:
            print("Failed to write element")
            print(traceback.format_exc())

    # Search for urls in <meta>
    elem = driver.find_elements(By.TAG_NAME, "meta")
    for el in elem:
        try:

            if el.get_attribute("http-equiv") and el.get_attribute("content"):
                if el.get_attribute("http-equiv").lower()  == "refresh":
                    m = re.search("url=(.*)", el.get_attribute("content"), re.IGNORECASE )
                    if not m:
                        continue
                    fresh_url = m.group(1)
                    full_fresh_url = urljoin( driver.current_url, fresh_url )
                    add_url_with_context(el, full_fresh_url, "meta", url_contexts, urls, driver)

        except StaleElementReferenceException as e:
            print("Stale pasta in from action")
        except:
            print("Failed to write element")
            print(traceback.format_exc())


    try:
        resps = driver.execute_script("return JSON.stringify(window_open_urls)")
        window_open_urls = json.loads(resps)
        for window_open_url in window_open_urls:
            full_window_open_url = urljoin( driver.current_url, window_open_url )
            add_url_with_context(None, full_window_open_url, "window.open", url_contexts, urls, driver)
    except Exception as e:
        logging.warning("Failed to extract window.open URLs: %s" % str(e))

    logging.debug("URLs from extract_urls %s" % str(urls) )

    return urls, url_contexts


