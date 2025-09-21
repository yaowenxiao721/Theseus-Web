# Functions.py contains general purpose functions can be utilized by
# the crawler.
import sys
from PIL import Image, ImageDraw
from selenium.webdriver.support.select import Select
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.keys import Keys
from selenium.common.exceptions import StaleElementReferenceException, TimeoutException, \
    UnexpectedAlertPresentException, NoSuchFrameException, NoAlertPresentException, ElementNotVisibleException, \
    InvalidElementStateException, NoSuchElementException
from urllib.parse import urlparse, parse_qs
from selenium.webdriver.common.by import By
import json
from datetime import datetime
from copy import deepcopy
import os
import traceback
import random
import re
import logging
import time
import html2text
import urllib.parse

import Classes
from extractors.Forms import extract_forms, parse_form
from llm_manager import LLMManager
from tools import get_accessible_name
from dotenv import load_dotenv

load_dotenv()

class bcolors:
    if sys.stdout.isatty():
        HEADER = '\033[95m'
        OKBLUE = '\033[94m'
        OKCYAN = '\033[96m'
        OKGREEN = '\033[92m'
        WARNING = '\033[93m'
        FAIL = '\033[91m'
        ENDC = '\033[0m'
        BOLD = '\033[1m'
        UNDERLINE = '\033[4m'
    else:
        HEADER = ''
        OKBLUE = ''
        OKCYAN = ''
        OKGREEN = ''
        WARNING = ''
        FAIL = ''
        ENDC = ''
        BOLD = ''
        UNDERLINE = ''

def send(driver, cmd, params={}):
    resource = "/session/%s/chromium/send_command_and_get_result" % driver.session_id
    url = driver.command_executor._url + resource
    body = json.dumps({'cmd': cmd, 'params': params})
    response = driver.command_executor._request('POST', url, body)
    if "status" in response:
        logging.error(response)


def add_script(driver, script):
    send(driver, "Page.addScriptToEvaluateOnNewDocument", {"source": script})

def xpath_row_to_cell(addr):
    # It seems impossible to click (and do other actions)
    # on a <tr> (Table row).
    # Instead, the onclick applies to all cells in the row.
    # Therefore, we pick the first cell.
    parts = addr.split("/")
    if (parts[-1][:2] == "tr"):
        addr += "/td[1]"
    return addr

def remove_alerts(driver):
    # Try to clean up alerts
    try:
        alert = driver.switch_to.alert
        alert.dismiss()
    except NoAlertPresentException:
        pass

def depth(edge):
    # method = edge.value.method
    depth = 1
    # while method != "get" and edge.parent:
    while edge.parent:
        depth = depth + 1
        edge = edge.parent
        # method = edge.value.method
    return depth

def capture_element_screenshot(driver, addr, filename):
    try:
        element = driver.find_element(By.XPATH, addr)
        screenshot_dir = os.path.join(os.path.dirname(__file__), "screenshots")
        if not os.path.exists(screenshot_dir):
            os.makedirs(screenshot_dir)
        driver.execute_script("arguments[0].scrollIntoView();", element)
        time.sleep(0.5)

        driver.save_screenshot(os.path.join(screenshot_dir, filename))

        location = element.location
        size = element.size

        x = location['x']
        y = location['y']
        width = location['x'] + size['width']
        height = location['y'] + size['height']

        im = Image.open(os.path.join(screenshot_dir, filename))
        draw = ImageDraw.Draw(im)

        draw.rectangle([x, y, width, height], outline='red', width=5)

        im.save(os.path.join(screenshot_dir, filename))
    except NoSuchElementException:
        logging.error("Could not find element to capture screenshot")
        return False
    except Exception as e:
        logging.error(str(e).splitlines()[0])
        return False
    return True

def extract_form_pages(path, is_crawl):
    if not is_crawl:
        return {}
    form_pages = {}
    index = -1
    for edge in path:
        index += 1
        if edge.value.method == "form" and edge.value.after_context:
            form_pages[index]= edge.value.after_context
    return form_pages

# Execute the path necessary to reach the state
def find_state(driver, graph, edge, is_crawl):
    path = rec_find_path(graph, edge)
    form_pages = extract_form_pages(path, is_crawl)

    text_maker = html2text.HTML2Text()
    text_maker.ignore_links = True
    last_edge = False
    successful = False
    index = -1
    for edge_in_path in path:
        index += 1
        if index == len(path) - 1:
            last_edge = True
        method = edge_in_path.value.method
        method_data = edge_in_path.value.method_data
        logging.info("find_state method "+str(method)+" "+str(method_data))

        before_num = 0
        before_page_context = ""
        before_page = ""
        if allow_edge(graph, edge_in_path):
            if is_crawl:
                time.sleep(0.1)
            if is_crawl and last_edge:
                time.sleep(0.5)
                before_num = len(driver.requests)
                before_page_context = text_maker.handle(driver.page_source)
                edge_in_path.value.set_before_context(before_page_context)
                before_page = driver.page_source
                edge_in_path.value.set_before_page(before_page)
            if method == "get":
                if "#" in edge_in_path.n2.value.url and not "#####" in edge_in_path.n2.value.url:
                    driver.get("http://localhost")
                driver.get(edge_in_path.n2.value.url)
                if is_crawl:
                    time.sleep(0.1)
            elif method == "form":
                if is_crawl and not last_edge:
                    continue
                form = method_data
                try:
                    form_fill(driver, form, is_crawl)
                    if is_crawl:
                        time.sleep(0.1)
                except Exception as e:
                    print(bcolors.OKGREEN+str(e).splitlines()[0]+bcolors.ENDC)
                    logging.error(str(e).splitlines()[0])
                    return False
            elif method == "ui_form":
                ui_form = method_data
                try:
                    ui_form_fill(driver, ui_form)
                except Exception as e:
                    print(bcolors.OKGREEN+str(e).splitlines()[0]+bcolors.ENDC)
                    logging.error(str(e).splitlines()[0])
                    return False
            elif method == "event":
                event = method_data
                execute_event(driver, event)
                remove_alerts(driver)
            elif method == "iframe":
                enter_status = enter_iframe(driver, method_data)
                if not enter_status:
                    logging.error("could not enter iframe (%s)" % method_data)
                    return False
            elif method == "javascript":
                js_code = edge_in_path.n2.value.url
                if "#####" in js_code:
                    js_code = js_code.split("#####")[1]
                js_code = js_code[11:]
                text = ""
                if "text:" in js_code:
                    text = js_code.split("text:")[1]
                    js_code = js_code.split("text:")[0]
                if "id:" in js_code:
                    id = js_code.split("id:")[1]
                    js_code = id
                    if " " in js_code:
                        js_code = js_code.split(" ")[0]
                    try:
                        els = driver.find_elements(By.ID, js_code)
                        for el in els:
                            try:
                                el_id = el.get_attribute("id")
                                el_text = el.text
                                if el_id == id and el_text == text:
                                    el.click()
                                    logging.info("Clicking on " + id)
                                    break
                            except NoSuchElementException as e:
                                print(bcolors.OKGREEN+"Could not find element to click on "+js_code+bcolors.ENDC)
                                logging.error("Could not find element to click on "+js_code)
                            except Exception as e:
                                logging.error("Could not click on "+js_code+" "+str(e).splitlines()[0])
                    except Exception as e:
                        print(bcolors.OKGREEN+"execute javascript " + js_code + " error: "+str(e).splitlines()[0]+bcolors.ENDC)
                elif "class_name:" in js_code:
                    class_name = js_code.split("class_name:")[1]
                    js_code = class_name
                    if " " in js_code:
                        js_code = js_code.split(" ")[0]
                    try:
                        els = driver.find_elements(By.CLASS_NAME, js_code)
                        for el in els:
                            try:
                                el_class_name = el.get_attribute("className")
                                el_text = el.text
                                if el_class_name == class_name and el_text == text:
                                    el.click()
                                    logging.info("Clicking on " + el_class_name)
                                    break
                            except NoSuchElementException as e:
                                print(bcolors.OKGREEN+"Could not find element to click on "+js_code+bcolors.ENDC)
                                logging.error("Could not find element to click on "+js_code)
                            except Exception as e:
                                logging.error("Could not click on "+js_code+" "+str(e).splitlines()[0])
                    except Exception as e:
                        print(bcolors.OKGREEN+"execute javascript " + js_code + " error: "+str(e).splitlines()[0]+bcolors.ENDC)
                        logging.error("execute javascript " + js_code + " error: "+str(e).splitlines()[0])
                elif "onclick:" in js_code:
                    onclick = js_code.split("onclick:")[1]
                    js_code = onclick
                    if " " in js_code:
                        js_code = js_code.split(" ")[0]
                    try:
                        els = driver.find_elements(By.XPATH, "//a[starts-with(@href, 'javascript:')]")
                        for el in els:
                            try:
                                el_onclick = el.get_attribute("onclick")
                                el_text = el.text
                                if el_onclick == onclick and el_text == text:
                                    el.click()
                                    logging.info("Clicking on " + onclick)
                                    break
                            except NoSuchElementException as e:
                                print(bcolors.OKGREEN+"Could not find element to click on "+js_code+bcolors.ENDC)
                                logging.error("Could not find element to click on "+js_code)
                            except Exception as e:
                                logging.error("Could not click on "+js_code+" "+str(e).splitlines()[0])
                    except Exception as e:
                        print(bcolors.OKGREEN+"execute javascript " + js_code + " error: "+str(e).splitlines()[0]+bcolors.ENDC)
                        logging.error("execute javascript " + js_code + " error: "+str(e).splitlines()[0])
                else:
                    try:
                        driver.execute_script(js_code)
                        logging.info("execute javascript " + js_code)
                    except Exception as e:
                        print(bcolors.OKGREEN+"execute javascript error: "+str(e).splitlines()[0]+bcolors.ENDC)
                        
                        logging.error("execute javascript error: "+str(e).splitlines()[0])
                        return False
            else:
                raise Exception("Can't handle method (%s) in find_state" % method)

            if last_edge:
                if is_crawl:
                    time.sleep(0.5)
                    try:
                        alert = driver.switch_to.alert
                        alertText = alert.text
                        logging.info("Removed alert: " + alertText)
                        alert.accept()
                    except:
                        logging.info("No alert removed (probably due to there not being any)")
                        pass
                    after_num = len(driver.requests)
                    after_page_context = text_maker.handle(driver.page_source)
                    edge_in_path.value.set_after_context(after_page_context)
                    after_page = driver.page_source
                    edge_in_path.value.set_after_page(after_page)
                    so = get_traffic(driver, graph, before_num, after_num, edge_in_path)
                    if so or after_page_context != before_page_context or after_page != before_page:
                        successful = True
                else:
                    successful = True

    return successful

def rec_find_form_path(graph, edge):
    if edge is None:
        return []
    path = []
    method = edge.value.method
    parent = edge.parent

    if method == "form":
        return path + [edge]
    else:
        return rec_find_form_path(graph, parent) + [edge]

def rec_find_path(graph, edge):
    path = []
    method = edge.value.method
    parent = edge.parent

    # This is the base case since the first request is always get.
    if method == "get":
        return path + [edge]
    else:
        return rec_find_path(graph, parent) + [edge]

def edge_sort(edge):
    if edge.value[0] == "form":
        return 0
    else:
        return 1

def check_edge(driver, graph, edge):
    logging.info("Check edge: " + str(edge))
    method = edge.value.method
    method_data = edge.value.method_data

    if method == "get":
        if allow_edge(graph, edge):
            purl = urlparse(edge.n2.value.url)
            if not purl.path in graph.data['urls']:
                graph.data['urls'][purl.path] = 0
            graph.data['urls'][purl.path] += 1
            return True
        else:
            logging.warning("Not allow to get %s" % str(edge.n2.value))
            return False
    elif method == "form":
        purl = urlparse(method_data.action)
        if not purl.path in graph.data['form_urls']:
            graph.data['form_urls'][purl.path] = 0
        graph.data['form_urls'][purl.path] += 1

        return True
    elif method == "event":
        return True
    else:
        return True

def get_traffic(driver, graph, before_num, after_num, edge):
    traffic_data = []

    from_url = graph.nodes[1].value.url

    filter_content_types = [
        "text/css",
        "application/javascript",
        "image/png",
        "font/woff2",
        "font/woff",
        "font/ttf",
        "image/x-icon",
        "application/font-woff",
        "image/vnd.microsoft.icon",
        "image/jpg",
        "image/jpeg",
        "image/gif",
        "multipart/form-data",
        "application/octet-stream",
        "application/x-www-form-urlencoded"
    ]

    fileter_postfixes = [
        ".js",
        ".css",
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".svg",
        ".ico",
        ".woff2",
        ".woff",
        ".ttf",
    ]

    heart_beats = [
        "api/sessions"
    ]

    for request in driver.requests[before_num:after_num]:
        if request.response:
            url = request.url
            for heart_beat in heart_beats:
                if url.endswith(heart_beat):
                    continue

            if same_origin(from_url, url):
                so = True
            else:
                continue

            response_need_filter = False
            for postfix in fileter_postfixes:
                if url.endswith(postfix):
                    response_need_filter = True
                    break

            response_headers = dict(request.response.headers)
            if response_headers:
                if "Content-Type" in response_headers:
                    content_type = response_headers["Content-Type"]
                    for filter_content_type in filter_content_types:
                        if filter_content_type in content_type:
                            response_need_filter = True
                            break
            request_need_filter = False
            request_headers = dict(request.headers)
            if request_headers:
                if "Content-Type" in request_headers:
                    content_type = request_headers["Content-Type"]
                    for filter_content_type in filter_content_types:
                        if filter_content_type in content_type:
                            request_need_filter = True
                            break

            try:
                request_body = request.body.decode('utf-8', errors='ignore')
            except:
                request_body = ""
            if request_need_filter:
                request_body = ""

            request_data = {
                "request_url": url,
                "request_method": request.method,
                "request_headers": dict(request.headers),
                "request_body": request_body,
                "response_status": request.response.status_code,
                "response_headers": dict(request.response.headers),
            }

            # Add response body only for non-static files
            if not response_need_filter:
                try:
                    request_data["response_body"] = request.response.body.decode('utf-8', errors='ignore')
                except Exception as e:
                    request_data["response_body_error"] = str(e).splitlines()[0]

                traffic_data.append(request_data)

    edge.value.set_request_datas(traffic_data)

    return len(traffic_data) > 0

def follow_edge(driver, graph, edge, is_crawl=False):
    logging.info("Follow edge: " + str(edge))
    method = edge.value.method
    method_data = edge.value.method_data
    if method == "get":
        text_maker = html2text.HTML2Text()
        before_num = 0
        before_page = ''
        if is_crawl:
            text_maker.ignore_links = True

            before_num = len(driver.requests)
            before_page = text_maker.handle(driver.page_source)
            edge.value.set_before_context(before_page)

        print(bcolors.OKGREEN+"GET "+str(edge.n2.value.url)+bcolors.ENDC)
        if "#" in edge.n2.value.url and not "#####" in edge.n2.value.url:
            driver.get("http://localhost")
        driver.get(edge.n2.value.url)

        if is_crawl:
            time.sleep(0.5)
            after_num = len(driver.requests)
            after_page = text_maker.handle(driver.page_source)
            edge.value.set_after_context(after_page)
            so = get_traffic(driver, graph, before_num, after_num, edge)
            if not (so or after_page != before_page):
                return None

    elif method == "form":
        logging.info("Form, do find_state")
        if not find_state(driver, graph, edge, is_crawl):
            logging.warning("Could not find state %s" % str(edge))
            graph.add_failed(edge)
            graph.visit_edge(edge)
            return None
    elif method == "event":
        logging.info("Event, do find_state")
        if not find_state(driver, graph, edge, is_crawl):
            logging.warning("Could not find state %s" % str(edge))
            graph.add_failed(edge)
            graph.visit_edge(edge)
            return None
    elif method == "iframe":
        logging.info("iframe, do find_state")
        if not find_state(driver, graph, edge, is_crawl):
            logging.warning("Could not find state %s" % str(edge))
            graph.add_failed(edge)
            graph.visit_edge(edge)
            return None
    elif method == "javascript":
        logging.info("Javascript, do find_state")
        if not find_state(driver, graph, edge, is_crawl):
            logging.warning("Could not find state %s" % str(edge))
            graph.add_failed(edge)
            graph.visit_edge(edge)
            return None
    elif method == "ui_form":
        logging.info("ui_form, do find_state")
        if not find_state(driver, graph, edge, is_crawl):
            logging.warning("Could not find state %s" % str(edge))
            graph.add_failed(edge)
            graph.visit_edge(edge)
            return None
    else:
        raise Exception("Can't handle method (%s) in next_unvisited_edge " % method)

    # Success
    return True

def compare_resource_operation(ro1, ro2):
    equal = True
    if "resource" in ro1 and "resource" in ro2:
        equal = equal and ro1["resource"] == ro2["resource"]
    if "operation" in ro1 and "operation" in ro2:
        equal = equal and ro1["operation"] == ro2["operation"]
    if "CRUD_type" in ro1 and "CRUD_type" in ro2:
        equal = equal and ro1["CRUD_type"] == ro2["CRUD_type"]
    return equal

def same_origin(u1, u2):
    p1 = urlparse(u1)
    p2 = urlparse(u2)

    return (p1.scheme == p2.scheme
            and p1.netloc == p2.netloc)

def allow_edge(graph, edge):
    crawl_edge = edge.value

    if crawl_edge.method == "get":
        to_url = edge.n2.value.url
    elif crawl_edge.method == "form":
        to_url = crawl_edge.method_data.action
    elif crawl_edge.method == "iframe":
        to_url = crawl_edge.method_data.src
    elif crawl_edge.method == "event":
        ignore = ["onerror"]  # Some events that we can't/don't trigger
        return not (crawl_edge.method_data.event in ignore)
    else:
        logging.info("Unsure about method %s, will allow." % crawl_edge.method)
        return True

    from_url = graph.nodes[1].value.url

    parsed_to_url = urlparse(to_url)

    # Relative links are fine. (Not sure about // links)
    if not parsed_to_url.scheme:
        return True

    # If the sceme is javascript we can't know to final destination, so we allow.
    if parsed_to_url.scheme == "javascript":
        return True

    so = same_origin(from_url, to_url)

    # TODO: More general solutions ? e.g regex patterns, counts etc.
    blacklisted_terms = []
    # For example
    # blacklisted_terms.extend( ["logout"] )
    if blacklisted_terms:
        logging.warning("Using blacklisted terms!")

    if to_url:
        bl = any([bt in to_url for bt in blacklisted_terms])
    else:
        bl = False

    # If we are in the same origin AND the request is not blacklisted
    # (Could just return (so and not bl) but this is clearer imho)
    if so and not bl:
        return True
    else:
        logging.debug("Different origins %s and %s" % (str(from_url), str(to_url)))
        return False

def execute_event(driver, do):
    logging.info("We need to trigger [" + do.event + "] on " + do.addr)

    do.addr = xpath_row_to_cell(do.addr)

    try:
        if do.event == "onclick" or do.event == "click":
            web_element = driver.find_element(By.XPATH, do.addr)
            logging.info("Click on ")

            if web_element.is_displayed():
                web_element.click()
            else:
                logging.warning("Trying to click on invisible element. Use JavaScript")
                driver.execute_script("arguments[0].click()", web_element)
        elif do.event == "ondblclick" or do.event == "dblclick":
            web_element = driver.find_element(By.XPATH, do.addr)
            logging.info("Double click on ")
            ActionChains(driver).double_click(web_element).perform()
        elif do.event == "onmouseout":
            logging.info("Mouseout on %s" % driver.find_element(By.XPATH, do.addr))
            driver.find_element(By.XPATH, do.addr).click()
            el = driver.find_element(By.XPATH, do.addr)
            # TODO find first element in body
            body = driver.find_element(By.XPATH, "/html/body")
            ActionChains(driver).move_to_element(el).move_to_element(body).perform()
        elif do.event == "onmouseover":
            logging.info("Mouseover on %s" % driver.find_element(By.XPATH, do.addr))
            el = driver.find_element(By.XPATH, do.addr)
            ActionChains(driver).move_to_element(el).perform()
        elif do.event == "onmousedown":
            logging.info("Click (mousedown) on %s" % driver.find_element(By.XPATH, do.addr))
            driver.find_element(By.XPATH, do.addr).click()
        elif do.event == "onmouseup":
            logging.info("Mouseup on %s" % driver.find_element(By.XPATH, do.addr))
            el = driver.find_element(By.XPATH, do.addr)
            ActionChains(driver).move_to_element(el).release().perform()
        elif do.event == "change" or do.event == "onchange":
            el = driver.find_element(By.XPATH, do.addr)
            logging.info("Change ")
            if el.tag_name == "select":
                # If need to change a select we try the different
                # options
                opts = el.find_elements(By.TAG_NAME, "option")
                for opt in opts:
                    try:
                        opt.click()
                    except UnexpectedAlertPresentException:
                        print(bcolors.OKGREEN+"Alert detected"+bcolors.ENDC)
                        alert = driver.switch_to.alert
                        alert.dismiss()
            else:
                # If ot a <select> we try to write
                el = driver.find_element(By.XPATH, do.addr)
                el.clear()
                el.send_keys("jAEkPot")
                el.send_keys(Keys.RETURN)
        elif do.event == "input" or do.event == "oninput":
            el = driver.find_element(By.XPATH, do.addr)
            el.clear()
            el.send_keys("jAEkPot")
            el.send_keys(Keys.RETURN)
            logging.info("oninput %s" % driver.find_element(By.XPATH, do.addr))

        elif do.event == "compositionstart":
            el = driver.find_element(By.XPATH, do.addr)
            el.clear()
            el.send_keys("jAEkPot")
            el.send_keys(Keys.RETURN)
            logging.info("Composition Start %s" % driver.find_element(By.XPATH, do.addr))

        else:
            logging.warning("Warning Unhandled event %s " % str(do.event))
    except NoSuchFrameException as e:
        print(bcolors.OKGREEN+"No such frame"+str(do)+bcolors.ENDC)
        logging.error("No such frame " + str(do))
    except NoSuchElementException as e:
        print(bcolors.OKGREEN+"No such element"+str(do)+bcolors.ENDC)
        logging.error("No such element " + str(do))
    except Exception as e:
        print(bcolors.OKGREEN+"Error"+str(do)+bcolors.ENDC)
        print(bcolors.OKGREEN+str(e).splitlines()[0]+bcolors.ENDC)

def form_fill_file(filename):
    dirname = os.path.dirname(__file__)
    path = os.path.join(dirname, 'form_files', filename)

    if filename != "jaekpot.jpg":
        path = os.path.join(dirname, 'form_files', 'dynamic', filename)
        dynamic_file = open(path, "w+")
        dynamic_file.write(filename)
        dynamic_file.close()

    return path


def fuzzy_eq(form1, form2):
    if form1.method != form2.method:
        return False
    form1_summary = get_form_summary(form1)
    form2_summary = get_form_summary(form2)
    return form1_summary == form2_summary

def update_value_with_js(driver, web_element, new_value):
    try:
        if new_value is not None:
            new_value = new_value.replace("'", "\\'")
        js_code = f'arguments[0].value = {json.dumps(str(new_value))}'
        driver.execute_script(js_code, web_element)
    except Exception as e:
        logging.error(str(e).splitlines()[0])
        logging.error("failed to update with JS " + str(new_value))

def form_fill(driver, target_form, is_crawl=False):
    logging.debug("Filling " + str(target_form))

    try:
        alert = driver.switch_to.alert
        alertText = alert.text
        logging.info("Removed alert: " + alertText)
        alert.accept()
    except Exception as e:
        logging.info("No alert removed (probably due to there not being any)"+str(e).splitlines()[0])
        pass

    form_wait_time = float(os.getenv('FORM_WAIT_TIME', '0.5'))
    time.sleep(form_wait_time)

    elem = driver.find_elements(By.TAG_NAME, "form")
    fill_success = False
    for el in elem:
        if not el.is_displayed():
            if not el.is_displayed():
                current_form = parse_form(el, driver)
                if str(current_form) != str(target_form):
                    logging.warning("Form " + str(current_form) + " is not displayed")
                    continue
                else:
                    logging.warning("Form " + str(current_form) + " is not displayed, but it is the target form")

        submit_buttons = []

        current_form = parse_form(el, driver)
        if not fuzzy_eq(current_form, target_form):
            logging.warning("current_form is "+str(current_form))
            continue
        logging.warning("Found form " + str(current_form))

        # TODO handle each element
        inputs = el.find_elements(By.TAG_NAME, "input")
        if not inputs:
            inputs = []
            logging.warning("No inputs founds, falling back to JavaScript")
            resps = driver.execute_script("return get_forms()")
            js_forms = json.loads(resps)
            for js_form in js_forms:
                current_form = Classes.Form()
                current_form.method = js_form['method']
                current_form.action = js_form['action']

                # TODO Need better COMPARE!
                if current_form.action == target_form.action and current_form.method == target_form.method:
                    for js_el in js_form['elements']:
                        web_el = driver.find_element(By.XPATH, js_el['xpath'])
                        inputs.append(web_el)
                    break

        buttons = el.find_elements(By.TAG_NAME, "button")
        inputs.extend(buttons)

        logging.warning("Filling inputs")
        for iel in inputs:
            try:
                iel_type = empty2none(iel.get_attribute("type"))
                iel_accessible_name = empty2none(get_accessible_name(driver, iel))
                iel_name = empty2none(iel.get_attribute("name"))
                iel_value = empty2none(iel.get_attribute("value"))
                if iel.get_attribute("type") == "radio":
                    form_iel = Classes.Form.RadioElement(
                                                    iel_type,
                                                    iel_accessible_name,
                                                    iel_name,
                                                    iel_value
                                                     )
                elif iel.get_attribute("type") == "checkbox":
                    form_iel = Classes.Form.CheckboxElement(
                                                     iel_type,
                                                     iel_accessible_name,
                                                     iel_name,
                                                     iel_value,
                                                     None)
                elif iel.get_attribute("type") == "submit":
                    form_iel = Classes.Form.SubmitElement(
                                                     iel_type,
                                                     iel_accessible_name,
                                                     iel_name,
                                                     iel_value,
                                                     None)
                else:
                    form_iel = Classes.Form.Element(
                                                     iel_type,
                                                     iel_accessible_name,
                                                     iel_name,
                                                     iel_value
                                                     )
                    logging.warning("Default handling for %s " % str(form_iel))

                if form_iel in target_form.inputs:
                    i = target_form.inputs[form_iel]

                    if iel.get_attribute("type") == "submit" or iel.get_attribute("type") == "image":
                        submit_buttons.append((iel, i))
                    elif iel.get_attribute("type") == "file":
                        if "/" in i.value:
                            logging.info("Cannot have slash in filename")
                        else:
                            try:
                                iel.send_keys(form_fill_file(i.value))
                                print(bcolors.OKGREEN+"Uploading file " + str(i.value) + " in " + str(form_iel)+bcolors.ENDC)
                                logging.info("Uploading file " + str(i.value) + " in " + str(form_iel))
                            except Exception as e:
                                logging.warning(
                                    "[inputs] Failed to upload file " + str(i.value) + " in " + str(form_iel))
                    elif iel.get_attribute("type") == "radio":
                        if i.override_value:
                            update_value_with_js(driver, iel, i.override_value)
                        if i.click:
                            iel.click()
                    elif iel.get_attribute("type") == "checkbox":
                        if i.override_value:
                            update_value_with_js(driver, iel, i.override_value)
                        if i.checked and not iel.get_attribute("checked"):
                            try:
                                iel.click()
                            except Exception as e:
                                logging.warning("[inputs] failed to click checkbox " + str(form_iel))
                                try:
                                    driver.execute_script("arguments[0].checked = true", iel)
                                except Exception as e:
                                    logging.warning("[inputs] failed to click checkbox with JS " + str(form_iel))
                                    update_value_with_js(driver, iel, i.value)
                    elif iel.get_attribute("type") == "hidden":
                        print(bcolors.OKGREEN+"IGNORE HIDDEN"+bcolors.ENDC)
                    elif iel.get_attribute("type") in ["text", "email", "url"]:
                        if iel.get_attribute("maxlength"):
                            try:
                                driver.execute_script("arguments[0].removeAttribute('maxlength')", iel)
                            except Exception as e:
                                logging.warning("[inputs] failed to change maxlength " + str(form_iel))
                        try:
                            iel.clear()
                            iel.send_keys(i.value)
                            print(bcolors.OKGREEN+"Filling in input " + str(form_iel) + " with " + str(i.value)+bcolors.ENDC)
                            logging.info("Filling in input " + str(form_iel) + " with " + str(i.value))
                        except Exception as e:
                            logging.warning("[inputs] failed to send keys for text to " + str(form_iel) + " with " + str(i.value) + " Trying javascript")
                            try:
                                js_code = f'arguments[0].value = {json.dumps(str(i.value))}'
                                driver.execute_script(js_code, iel)
                            except Exception as e:
                                logging.error(str(e).splitlines()[0])
                                logging.error("[inputs] also failed with JS " + str(form_iel) + " with " + str(i.value))
                    elif iel.get_attribute("type") == "password":
                        try:
                            iel.clear()
                            iel.send_keys(i.value)
                            print(bcolors.OKGREEN+"Filling in input " + str(form_iel) + " with " + str(i.value)+bcolors.ENDC)
                            logging.info("Filling in input " + str(form_iel) + " with " + str(i.value))
                        except Exception as e:
                            logging.error(str(e).splitlines()[0])
                            logging.warning("[inputs] failed to send keys for password to " + str(form_iel) + " with " + str(i.value)+" Trying javascript")
                    else:
                        logging.warning("[inputs] using default clear/send_keys for " + str(form_iel) + " with " + str(i.value))
                        try:
                            iel.clear()
                            iel.send_keys(i.value)
                            print(bcolors.OKGREEN+"Filling in input " + str(form_iel) + " with " + str(i.value)+bcolors.ENDC)
                            logging.info("Filling in input " + str(form_iel) + " with " + str(i.value))
                        except Exception as e:
                            logging.error(str(e).splitlines()[0])
                            logging.warning("[inputs] failed to send keys to " + str(form_iel) + " with " + str(i.value)+" Trying javascript")
                            update_value_with_js(driver, iel, i.value)
                else:
                    logging.warning("[inputs] could NOT FIND " + str(form_iel))
                    logging.warning("--" + str(target_form.inputs))
                logging.info("Filling in input " + iel.get_attribute("name"))

            except Exception as e:
                logging.error("Could not fill in form")
                logging.error(str(e).splitlines()[0])

        # <select>
        selects = el.find_elements(By.TAG_NAME, "select")
        logging.warning("Filling selects")
        for select in selects:
            form_select = Classes.Form.SelectElement("select", empty2none(get_accessible_name(driver, select)), empty2none(select.get_attribute("name")), empty2none(select.get_attribute("value")))
            if form_select in target_form.inputs:
                i = target_form.inputs[form_select]
                selenium_select = Select(select)
                options = selenium_select.options
                if i.override_value and options:
                    update_value_with_js(driver, options[0], i.override_value)
                else:
                    for option in options:
                        if option.get_attribute("value") == i.selected:
                            try:
                                driver.execute_script("arguments[0].selected = true;", option)
                                logging.info("Filling in input " + str(form_select) + " with " + str(i.selected))
                            except Exception as e:
                                logging.error(str(e).splitlines()[0])
                                logging.error("Could not click on " + str(form_select) + " with " + str(i.selected)+" trying JS")
                                update_value_with_js(driver, select, i.selected)
                            break
            else:
                logging.warning("[selects] could NOT FIND " + str(form_select))

        # <textarea>
        textareas = el.find_elements(By.TAG_NAME, "textarea")
        logging.warning("Filling textareas")
        for ta in textareas:
            form_ta = Classes.Form.Element(ta.get_attribute("type"),
                                           empty2none(get_accessible_name(driver, ta)),
                                           empty2none(ta.get_attribute("name")),
                                           empty2none(ta.get_attribute("value")))
            if form_ta in target_form.inputs:
                i = target_form.inputs[form_ta]
                try:
                    ta.clear()
                    ta.send_keys(i.value)
                    print(bcolors.OKGREEN+"Filling in input " + str(form_ta) + " with " + str(i.value)+bcolors.ENDC)
                    logging.info("Filling in input " + str(form_ta) + " with " + str(i.value))
                except Exception as e:
                    logging.error(str(e).splitlines()[0])
                    logging.info("[inputs] failed to send keys for textareas to " + str(form_ta) + " with " + str(i.value)+" Trying javascript")
                    update_value_with_js(driver, ta, i.value)
            else:
                logging.warning("[textareas] could NOT FIND " + str(form_ta))

        # <iframes>
        iframes = el.find_elements(By.TAG_NAME, "iframe")
        logging.warning("Filling iframes")
        for iframe in iframes:
            try:
                iframe_id = iframe.get_attribute("id")
                driver.switch_to.frame(iframe)
                iframe_body = driver.find_element(By.TAG_NAME, "body")
                accessible_name = get_accessible_name(driver, iframe_body)
                if accessible_name:
                    accessible_name = accessible_name
                elif iframe_body.get_attribute("data-id"):
                    accessible_name = iframe_body.get_attribute("data-id")
                logging.debug("Found iframe accessible_name "+str(accessible_name))
                form_iframe = Classes.Form.Element("iframe", accessible_name, iframe_id, "")
                driver.switch_to.default_content()

                if form_iframe in target_form.inputs:
                    i = target_form.inputs[form_iframe]
                    try:
                        iframe_id = i.name
                        driver.switch_to.frame(iframe)
                        iframe_bodies = driver.find_elements(By.TAG_NAME, "body")
                        for iframe_body in iframe_bodies:
                            logging.warning("iframe_body is : "+iframe_body.get_attribute("data-id"))
                            if iframe_body.get_attribute("contenteditable") == "true":
                                iframe_body.clear()
                                iframe_body.send_keys(i.value)
                                print(bcolors.OKGREEN+"Filling in input "+str(form_iframe)+" with "+str(i.value)+bcolors.ENDC)
                                logging.info("Filling in input "+str(form_iframe)+" with "+str(i.value))
                            else:
                                logging.error("Body not contenteditable, was during parse")

                        driver.switch_to.default_content()

                    except InvalidElementStateException as e:
                        logging.error("Could not clear " + str(form_iframe))
                        logging.error(str(e).splitlines()[0])
                        driver.switch_to.default_content()
                else:
                    logging.warning("[iframes] could NOT FIND " + str(form_iframe))
            except Exception as e:
                logging.error("Switch iframe error " + str(e).splitlines()[0])
                driver.switch_to.default_content()


        is_slider_captcha = False
        a_tags = el.find_elements(By.TAG_NAME, "a")
        logging.warning("Filling a_tags")
        for a_tag in a_tags:
            if "Login" in a_tag.get_attribute("id"):
                form_submit = Classes.Form.SubmitElement("submit", get_accessible_name(driver, a_tag), a_tag.get_attribute("name"),
                                                         a_tag.get_attribute("value"), None)
                form_submit.use = True
                submit_buttons.append((a_tag, form_submit))
                is_slider_captcha = True

        # submit
        if submit_buttons:
            logging.info("form_fill Clicking on submit button")

            has_use = False

            for submit_button in submit_buttons:
                (selenium_submit, form_submit) = submit_button
                print(bcolors.OKGREEN+"Clicking on submit button "+str(form_submit)+bcolors.ENDC)
                logging.info("form_fill Clicking on submit button " + str(form_submit))


                if form_submit.use:
                    has_use = True
                    try:
                        selenium_submit.click()
                        break
                    except ElementNotVisibleException as e:
                        logging.warning("Cannot click on invisible submit button: " + str(
                            target_form) + " trying JavaScript click")
                        logging.info("form_fill Javascript submission of form after failed submit button click")

                        driver.execute_script("arguments[0].click()", selenium_submit)

                        # Also try submitting the full form, shouldn't be needed
                        try:
                            el.submit()
                        except Exception as e:
                            logging.info("Could not submit form, could be good!")

                    except Exception as e:
                        logging.warning("Cannot click on submit button: " + str(target_form))
                        logging.info("form_fill Javascript submission of form after failed submit button click")
                        try:
                            driver.execute_script("arguments[0].click()", selenium_submit)
                            logging.debug("Executed JavaScript click on submit button")
                            el.submit()
                        except Exception as e:
                            logging.info("Could not submit form, could be good! "+str(e).splitlines()[0])

                # Some forms show an alert with a confirmation
                try:
                    alert = driver.switch_to.alert
                    alertText = alert.text
                    logging.info("Removed alert: " + alertText)
                    alert.accept()
                except:
                    logging.info("No alert removed (probably due to there not being any)")
                    pass
            if not has_use:
                logging.warning("No submit button with use found")
                el.submit()
        else:
            logging.info("form_fill Javascript submission of form")
            el.submit()

        # Check if submission caused an "are you sure" alert
        try:
            alert = driver.switch_to.alert
            alertText = alert.text
            logging.info("Removed alert: " + alertText)
            alert.accept()
        except:
            logging.info("No alert removed (probably due to there not being any)")

        # End of form fill if everything went well
        fill_success = True
        break

    if fill_success:
        logging.info("Form fill success")
        return True

    logging.error("error no form found (url:%s, form:%s)" % (driver.current_url, target_form))
    return False

def ui_form_fill(driver, target_form):
    logging.debug("Filling ui_form " + str(target_form))

    # Ensure we don't have any alerts before filling in form
    try:
        alert = driver.switch_to.alert
        alertText = alert.text
        logging.info("Removed alert: " + alertText)
        alert.accept()
    except:
        logging.info("No alert removed (probably due to there not being any)")
        pass

    for source in target_form.sources:
        web_element = driver.find_element(By.XPATH, source['xpath'])

        if web_element.get_attribute("maxlength"):
            try:
                driver.execute_script("arguments[0].removeAttribute('maxlength')", web_element)
            except Exception as e:
                logging.warning("[inputs] failed to change maxlength " + str(web_element))

        input_value = source['value']
        try:
            web_element.clear()
            web_element.send_keys(input_value)
            print(bcolors.OKGREEN+"Filling in input "+str(source)+ " with "+str(input_value)+bcolors.ENDC)
            logging.info("Filling in input "+str(source)+" with "+str(input_value))
        except Exception as e:
            logging.warning("[inputs] failed to send keys for ui_form to " + str(input_value) + " with "+str(input_value)+" Trying javascript")
            try:
                driver.execute_script("arguments[0].value = '" + str(input_value) + "'", web_element)
            except Exception as e:
                logging.error(str(e).splitlines()[0])
                logging.error(traceback.format_exc())
                logging.error("[inputs] also failed with JS " + str(web_element))

    submit_element = driver.find_element(By.XPATH, target_form.submit)
    submit_element.click()

def get_form_summary(form):
    summary = ""
    for form_el in form.inputs.values():
        summary += str(form_el.itype)

    return summary

def safe_int_less_than(value, threshold):
    try:
        return int(value) < threshold
    except (ValueError, TypeError):
        return False

def set_standard_values(driver, old_form, llm_manager, tokenizer, is_login_form, form_context=None):
    form = deepcopy(old_form)
    first_radio = True

    dom_context = ''
    action_url = ''
    form_fields = ''
    form_summary = ''
    if form_context:
        dom_context = dom_context_format(form_context['dom_context'], tokenizer)
        action_url = json.dumps(form_context['action_url'])
        form_fields = str(old_form)
        form_summary = get_form_summary(old_form)

    for form_el in form.inputs.values():
        if form_el.itype == "file":
            form_el.value = "jaekpot.jpg"
        elif form_el.itype == "radio":
            if first_radio:
                form_el.click = True
                first_radio = False
            # else don't change the value
        elif form_el.itype == "checkbox":
            # Just activate all checkboxes
            form_el.checked = True
        elif form_el.itype == "submit":
            value = random.choice(['ignore', 'fill'])
            if value == 'ignore':
                form_el.use = False
            else:
                form_el.use = True
        elif form_el.itype == "image":
            form_el.use = False
        elif form_el.itype == "select":
            if form_el.selected is None or form_el.selected == "" or safe_int_less_than(form_el.selected, 1):
                if form_el.options:
                    if not form_el.selected:
                        form_el.selected = form_el.options[0]
                else:
                    logging.warning(str(form_el) + " has no options")
        elif form_el.itype == "text":
            if form_el.value is None:
                if form_el.value is not None and form_el.value.isdigit():
                    form_el.value = random.randint(0, 100)
                elif form_el.name and "email" in form_el.name.lower() and is_login_form:
                    form_el.value = os.getenv("USER")
                elif form_el.name and "user" in form_el.name.lower() and is_login_form:
                    form_el.value = os.getenv("USER")
                else:
                    form_el.value = "Random"
        elif form_el.itype == "textarea":
            if form_el.value is None:
                form_el.value = "Random"
        elif form_el.itype == "email":
            form_el.value = "user1@test.com"
        elif form_el.itype == "hidden":
            pass
        elif form_el.itype == "password":
            if is_login_form:
                form_el.value = os.getenv("PASS")
            else:
                form_el.value = os.getenv("PASS")
        elif form_el.itype == "number":
            # TODO Look at min/max/step/maxlength to pick valid numbers
            form_el.value = "1"
        elif form_el.itype == "iframe":
            if form_el.value == "":
                form_el.value = "Random"
        elif form_el.itype == "button":
            pass
        elif form_el.itype == "search":
            if form_el.value is None:    
                form_el.value = "Random"
        else:
            logging.warning(str(form_el) + " was handled by default")
            form_el.value = "jAEkPot"

    return form

def set_submits(forms):
    new_forms = set()
    for form in forms:
        submits = set()
        for form_el in form.inputs.values():
            if form_el.itype == "submit" or form_el.itype == "image":
                submits.add(form_el)

        if form.a_tags.values():
            for a_tag in form.a_tags.keys():
                if "Login"  in a_tag:
                    submits.add(form_el)

        if len(submits) > 1:
            for submit in submits:
                new_form = deepcopy(form)
                for new_form_el in new_form.inputs.values():
                    if new_form_el.itype == "submit" and new_form_el == submit:
                        new_form_el.use = True

                new_forms.add(new_form)
        elif len(submits) == 1:
            submits.pop().use = True
            new_forms.add(form)



    return new_forms

def set_checkboxes(forms):
    new_forms = set()
    for form in forms:
        new_form = deepcopy(form)
        for new_form_el in new_form.inputs.values():
            if new_form_el.itype == "checkbox":
                new_form_el.checked = False
                new_forms.add(form)
                new_forms.add(new_form)
    return new_forms

def set_form_values(driver, forms, llm_manager, tokenizer, is_login_form, form_context=None):
    logging.info("set_form_values got " + str(len(forms)))
    new_forms = set()
    for old_form in forms:
        new_forms.add( set_standard_values(driver, old_form, llm_manager, tokenizer, is_login_form, form_context) )

    check_box_count = 0
    for form in new_forms:
        for form_el in form.inputs.values():
            if form_el.itype == "checkbox":
                check_box_count += 1

    logging.info("set_form_values check_box_count " + str(check_box_count))

    logging.info("set_form_values returned " + str(len(new_forms)))

    return new_forms

def enter_iframe(driver, target_frame):
    elem = driver.find_elements(By.TAG_NAME, "iframe")
    elem.extend(driver.find_elements(By.TAG_NAME, "frame"))

    for el in elem:
        try:
            src = None
            i = None

            if el.get_attribute("src"):
                src = el.get_attribute("src")
            if el.get_attribute("id"):
                i = el.get_attribute("i")

            current_frame = Classes.Iframe(i, src)
            if current_frame == target_frame:
                driver.switch_to.frame(el)
                return True

        except StaleElementReferenceException as e:
            logging.error("Stale pasta in from action")
            return False
        except Exception as e:
            logging.error("Unhandled error: " + str(e).splitlines()[0])
            return False
    return False

def find_login_form(driver, graph, early_state=False):
    logging.info("Finding login form in " + driver.current_url)
    forms, form_contexts = extract_forms(driver)
    for form in forms:
        count = 0
        for input in form.inputs:
            if input.itype != "hidden" and input.itype != "button":
                count += 1
        for form_input in form.inputs:
            if form_input.itype == "password":
                max_input_for_login = 6
                if count > max_input_for_login:
                    logging.info("Too many inputs for a login form, " + str(form))
                    continue

                # We need to make sure that the form is part of the graph
                logging.info("NEED TO LOGIN FOR FORM: " + str(form))

                return form
    return None

# Returns None if the string is empty, otherwise just the string
def empty2none(s):
    if not s:
        return None
    else:
        return s

def dom_context_format(dom_context, tokenizer):
    dom_context_prompt = ''
    if 'current_node' in dom_context:
        current_node = dom_context['current_node']
        dom_context_prompt += 'current node dom context contains: '
        if isinstance(current_node, str):
            dom_context_prompt += f"html: {current_node}"
        elif isinstance(current_node, dict):
            if 'tag_name' in current_node:
                dom_context_prompt += f"tag_name: {current_node['tag_name']}, "
            if 'attributes' in current_node:
                dom_context_prompt += f"attributes: {current_node['attributes']}, "
            if 'text' in current_node:
                dom_context_prompt += f"text: {current_node['text']}"
        else:
            dom_context_prompt += f"{current_node}"
    if 'parent_node' in dom_context:
        parent_node = dom_context['parent_node']
        dom_context_prompt += ', parent node dom context contains: '
        if isinstance(parent_node, str):
            dom_context_prompt += f"html: {parent_node}"
        elif isinstance(parent_node, dict):
            if 'tag_name' in parent_node:
                dom_context_prompt += f"tag_name: {parent_node['tag_name']}, "
            if 'attributes' in parent_node:
                dom_context_prompt += f"attributes: {parent_node['attributes']}, "
            if 'text' in parent_node:
                dom_context_prompt += f"text: {parent_node['text']}"
        else:
            dom_context_prompt += f"{parent_node}"
    if 'sibling_nodes' in dom_context:
        sibling_nodes = dom_context['sibling_nodes']
        dom_context_prompt += ', sibling nodes dom context contains: '
        index = 0
        if isinstance(sibling_nodes, str):
            dom_context_prompt += f"html: {sibling_nodes}"
        elif isinstance(sibling_nodes, list):
            for sibling_node in sibling_nodes:
                dom_context_prompt += f"sibling node {index} contains: "
                if isinstance(sibling_node, str):
                    dom_context_prompt += f"html: {sibling_node}"
                elif isinstance(sibling_node, dict):
                    dom_context_prompt += f"tag_name: {sibling_node['tag_name']}, "
                    dom_context_prompt += f"attributes: {sibling_node['attributes']}, "
                    dom_context_prompt += f"text: {sibling_node['text']}, "
                else:
                    dom_context_prompt += f"{sibling_node}"
                index += 1
        else:
            dom_context_prompt += f"{sibling_nodes}"
    length = len(tokenizer.encode(dom_context_prompt))
    MAX_CONTEXT_LENGTH = int(os.getenv("MAX_CONTEXT_LENGTH", 65536))
    DOM_CONTEXT_LENGTH = MAX_CONTEXT_LENGTH*0.7
    if length > DOM_CONTEXT_LENGTH:
        logging.warning("Prompt too long: " + str(length) + " " + str(len(dom_context_prompt)))
        dom_context_prompt = dom_context_prompt[:int(DOM_CONTEXT_LENGTH)]
    if 'page_title' in dom_context:
        dom_context_prompt += f", page title: {dom_context['page_title']}"

    return dom_context_prompt

semantic_cache = {}
function_llm_manger = LLMManager(os.getenv("API_KEY"), os.getenv("BASE_URL"), os.getenv("MODEL_NAME"))

def is_param_important(param_name, param_value1, param_value2, url1, url2, url_template):
    if param_name.startswith("PATH_PARAM_"):
        return True
    if url_template not in semantic_cache:
        semantic_cache[url_template] = {}
    if param_name not in semantic_cache[url_template]:
        semantic_cache[url_template][param_name] = {}
    if "is_semantically_important" in semantic_cache[url_template][param_name]:
        return semantic_cache[url_template][param_name]["is_semantically_important"]
    if "value" not in semantic_cache[url_template][param_name]:
        semantic_cache[url_template][param_name]["value"] = []
    if param_value1 not in semantic_cache[url_template][param_name]["value"]:
        semantic_cache[url_template][param_name]["value"].append(param_value1)
    if param_value2 not in semantic_cache[url_template][param_name]["value"]:
        semantic_cache[url_template][param_name]["value"].append(param_value2)
    if len(semantic_cache[url_template][param_name]["value"]) > 2:
        prompt = f"""URL: {url1}, Parameter Name: {param_name}, Parameter Value: {str(semantic_cache[url_template][param_name]["value"])}"""
        start = time.time()
        formatted_start = datetime.fromtimestamp(start).strftime('%Y-%m-%d %H:%M:%S')
        print(bcolors.OKGREEN+"start to check if parameter is semantically important "+str(formatted_start)+bcolors.ENDC)
        generated_data = function_llm_manger.identify_semantically_important_parameter(prompt)
        print(bcolors.OKGREEN+"end to check if parameter is semantically important "+str(time.time()-start)+bcolors.ENDC)
        semantic_cache[url_template][param_name]["is_semantically_important"] = generated_data.get("semantically important", True)
        print(bcolors.OKGREEN+"url "+url1+" semantic_param_analysis for "+str(param_name)+" is "+str(semantic_cache[url_template][param_name]["is_semantically_important"])+bcolors.ENDC)
        return semantic_cache[url_template][param_name]["is_semantically_important"]

    return False

def extract_all_query_params(query_str):
    pairs = re.split(r"[&;]", query_str)
    params = {}
    for pair in pairs:
        if '=' in pair:
            k, v = pair.split('=', 1)
            params.setdefault(k, []).append(v)
        elif pair:
            params.setdefault(pair, []).append(pair)
    return params

def extract_all_parameters(url: str):
    parsed = urllib.parse.urlparse(url)
    params = {}

    # From query string
    params.update(urllib.parse.parse_qs(parsed.query, keep_blank_values=True))
    query_params = extract_all_query_params(parsed.query)
    for k, v in query_params.items():
        if len(v) > 0:
            params[k] = v[0]
        else:
            params[k] = k

    frag_params = extract_all_query_params(parsed.fragment)
    for k, v in frag_params.items():
        if len(v) > 0:
            params[k] = v[0]
        else:
            params[k] = k

    path_parts = parsed.path.strip("/").split("/")
    for i in range(len(path_parts)):
        part = path_parts[i]
        if "=" in part:
            k, v = part.split("=", 1)
            params[k] = v
        elif ":" in part:
            k, v = part.split(":", 1)
            params[k] = v
        elif part.isdigit():
            PATH_PARAM = "PATH_PARAM_"+str(i)
            params[PATH_PARAM] = part
    return params

def get_url_template(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    url_template = parsed.scheme + "://" + parsed.netloc
    path_parts = parsed.path.strip("/").split("/")
    for i in range(len(path_parts)):
        part = path_parts[i]
        if "=" in part:
            url_template += "/" + part.split("=")[0]
        elif ":" in part:
            url_template += "/" + part.split(":")[0]
        elif part.isdigit():
            url_template += "/PATH_PARAM_"+str(i)
        else:
            url_template += "/" + part
    if "=" in parsed.fragment:
        url_template += "#" + parsed.fragment.split("=")[0]
    elif ":" in parsed.fragment:
        url_template += "#" + parsed.fragment.split(":")[0]
    elif parsed.fragment.isdigit():
        url_template += "#PATH_PARAM_"+str(len(path_parts))
    else:
        url_template += "#" + parsed.fragment
    return url_template

# === Compare two URLs for semantic equivalence ===
def are_urls_equivalent(url1: str, url2: str) -> bool:
    if url1 == url2:
        return True
    if url1 is None or url2 is None:
        return False
    url1 = str(url1)
    url2 = str(url2)
    try:
        parsed1 = urllib.parse.urlparse(url1)
        parsed2 = urllib.parse.urlparse(url2)
    except Exception as e:
        print(bcolors.OKGREEN+"Error parsing URLs: "+str(e).splitlines()[0]+bcolors.ENDC)
        return False

    # Compare path
    url1_template = parsed1.scheme + "://" + parsed1.netloc
    url2_template = parsed2.scheme + "://" + parsed2.netloc
    path1_parts = parsed1.path.strip("/").split("/")
    path2_parts = parsed2.path.strip("/").split("/")
    if len(path1_parts) != len(path2_parts):
        return False
    path_length = len(path1_parts)
    for i in range(path_length):
        if "=" in path1_parts[i] and "=" in path2_parts[i]:
            url1_template += "/" + path1_parts[i].split("=")[0]
            url2_template += "/" + path2_parts[i].split("=")[0]
        elif ":" in path1_parts[i] and ":" in path2_parts[i]:
            url1_template += "/" + path1_parts[i].split(":")[0]
            url2_template += "/" + path2_parts[i].split(":")[0]
        elif path1_parts[i].isdigit() and path2_parts[i].isdigit():
            url1_template += "/PATH_PARAM_"+str(i)
            url2_template += "/PATH_PARAM_"+str(i)
            continue
        elif path1_parts[i] == path2_parts[i]:
            url1_template += "/" + path1_parts[i]
            url2_template += "/" + path2_parts[i]
        else:
            return False
    if parsed1.netloc != parsed2.netloc:
        return False
    if parsed1.scheme != parsed2.scheme:
        return False
    if "=" in parsed1.fragment and "=" in parsed2.fragment:
        url1_template += "#" + parsed1.fragment.split("=")[0]
        url2_template += "#" + parsed2.fragment.split("=")[0]
    elif ":" in parsed1.fragment and ":" in parsed2.fragment:
        url1_template += "#" + parsed1.fragment.split(":")[0]
        url2_template += "#" + parsed2.fragment.split(":")[0]
    elif parsed1.fragment.isdigit() and parsed2.fragment.isdigit():
        url1_template += "#PATH_PARAM_"+str(path_length)
        url2_template += "#PATH_PARAM_"+str(path_length)
    elif parsed1.fragment == parsed2.fragment:
        url1_template += "#" + parsed1.fragment
        url2_template += "#" + parsed2.fragment
    else:
        return False

    if url1_template!= url2_template:
        return False

    # Extract and filter parameters
    params1 = extract_all_parameters(url1)
    params2 = extract_all_parameters(url2)

    # All keys union
    all_keys = set(params1.keys()) | set(params2.keys())
    if all_keys != set(params1.keys()) & set(params2.keys()):
        return False
    for k in all_keys:
        v1 = params1.get(k)
        v2 = params2.get(k)
        if v1 != v2:
            # If values differ, and param is important → not equivalent
            if is_param_important(k, v1, v2, url1, url2, url1_template):
                return False
    return True

def get_element_text(driver, element):
    return driver.execute_script("return arguments[0].textContent;", element).strip()