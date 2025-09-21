/*
 * Simulate.js from https://github.com/airportyh/simulate.js
 */
!function() {
	function extend(dst, src) {
		for ( var key in src)
			dst[key] = src[key]
		return src
	}
	var Simulate = {
		event : function(element, eventName) {
			if (document.createEvent) {
				var evt = document.createEvent("HTMLEvents")
				evt.initEvent(eventName, true, true)
				element.dispatchEvent(evt)
			} else {
				var evt = document.createEventObject()
				element.fireEvent('on' + eventName, evt)
			}
		},
		keyEvent : function(element, type, options) {
			var evt, e = {
				bubbles : true,
				cancelable : true,
				view : window,
				ctrlKey : false,
				altKey : false,
				shiftKey : false,
				metaKey : false,
				keyCode : 0,
				charCode : 0
			}
			extend(e, options)
			if (document.createEvent) {
				try {
					evt = document.createEvent('KeyEvents')
					evt.initKeyEvent(type, e.bubbles, e.cancelable, e.view,
							e.ctrlKey, e.altKey, e.shiftKey, e.metaKey,
							e.keyCode, e.charCode)
					element.dispatchEvent(evt)
				} catch (err) {
					evt = document.createEvent("Events")
					evt.initEvent(type, e.bubbles, e.cancelable)
					extend(evt, {
						view : e.view,
						ctrlKey : e.ctrlKey,
						altKey : e.altKey,
						shiftKey : e.shiftKey,
						metaKey : e.metaKey,
						keyCode : e.keyCode,
						charCode : e.charCode
					})
					element.dispatchEvent(evt)
				}
			}
		}
	}
	Simulate.keypress = function(element, chr) {
		var charCode = chr.charCodeAt(0)
		this.keyEvent(element, 'keypress', {
			keyCode : charCode,
			charCode : charCode
		})
	}
	Simulate.keydown = function(element, chr) {
		var charCode = chr.charCodeAt(0)
		this.keyEvent(element, 'keydown', {
			keyCode : charCode,
			charCode : charCode
		})
	}
	Simulate.keyup = function(element, chr) {
		var charCode = chr.charCodeAt(0)
		this.keyEvent(element, 'keyup', {
			keyCode : charCode,
			charCode : charCode
		})
	}
	Simulate.change = function(element) {
		var evt = document.createEvent("HTMLEvents");
		evt.initEvent("change", false, true);
		element.dispatchEvent(evt);

	}
	//Simulate.click = function(element){
	//	element.click();
	//}
	var events = ['click','focus', 'blur', 'dblclick', 'input', 'mousedown',
			'mousemove', 'mouseout', 'mouseover', 'mouseup', 'resize',
			'scroll', 'select', 'submit', 'load', 'unload', 'mouseleave' ]
	for (var i = events.length; i--;) {
		var event = events[i]
		Simulate[event] = (function(evt) {
			return function(element) {
				this.event(element, evt)
			}
		}(event))
	}
	if (typeof module !== 'undefined') {
		module.exports = Simulate
	} else if (typeof window !== 'undefined') {
		window.Simulate = Simulate
	} else if (typeof define !== 'undefined') {
		define(function() {
			return Simulate
		})
	}
}();
/*
 * From down here
 * 
 *Copyright (C) 2015 Constantin Tschuertz
 * 
 * This program is free software: you can redistribute it and/or modify
 * it under the terms of the GNU General Public License as published by
 * the Free Software Foundation, either version 3 of the License, or
 * any later version.
 *
 *This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 * GNU General Public License for more details.
 *
 * You should have received a copy of the GNU General Public License
 * along with this program.  If not, see <http://www.gnu.org/licenses/>.
 *
 */

// Test for send wrapper (TODO)
need_to_wait = false;
var original = XMLHttpRequest.prototype['open'];
XMLHttpRequest.prototype['open'] = function() {
  need_to_wait = true;
  return original.apply(this, arguments);
}
// End of test 

function callbackWrap(object, property, argumentIndex, wrapperFactory) {
	var original = object[property];
	object[property] = function() {
		wrapperFactory(this, arguments);
		return original.apply(this, arguments);
	}
	return original;
}

var max_waiting_time = 65000
var min_waiting_time = 0

function timingCallbackWrap(object, property, argumentIndex, wrapperFactory) {
	var original = object[property];

	object[property] = function() {
		if (arguments[1] > max_waiting_time) {
			arguments[1] = max_waiting_time
		}
		wrapperFactory(this, arguments);
		return original.apply(this, arguments);
	}
	return original;
}

function callInterceptionWrapper(object, property, argumentIndex,
		wrapperFactory) {
	var original = object[property];
	object[property] = function() {
		wrapperFactory(this, arguments);
		return null;
	}
	return original;
}

function XMLHTTPObserverOpen(elem, args) {
	resp = {
		"url" : args[1],
		"method" : args[0]
	};
	random_num =  Math.floor((Math.random() * 10000) + 1);
	elem.jaeks_id = random_num;
    timeouts.push(resp);
    console.log("Observer " + resp);
}

function XMLHTTPObserverSend(elem, args) {
	elems = []
	for (i = 0; i < args.length; i++) {
		elems.push(args[i])
	}
	resp = {
		"parameters" : elems
	};
    console.log("Send " + resp);
}

window_open_urls = []
function openWrapper(elem, args) {
    window_open_urls.push(args[0])
}

timeouts = Array();
function timeoutWrapper(elem, args) {
	function_id = MD5(args[0].toString());
	resp = {
		"function_id" : function_id,
		"function_name" : args[0].name,
		"time" : args[1]
	};
    timeouts.push(resp);
}

function intervallWrapper(elem, args) {
	function_id = MD5(args[0].toString());
	resp = {
		"function_id" : function_id,
		"time" : args[1]
	};
}

function getXPath(element) {

	try {
		var xpath = '';

    // Updated by Benjamin
    if (element.id) {
      return '//*[@id="'+element.id+'"]';
    }

		for (; element && element.nodeType == 1; element = element.parentNode) {

            if (!element.parentNode || !element.tagName) {
               break;
            }

			var sibblings = element.parentNode.childNodes;

			var same_tags = []
			for (var i = 0; i < sibblings.length; i++) { // collecting same
				if (element.tagName === sibblings[i].tagName) {
					same_tags[same_tags.length] = sibblings[i]
				}
			}

			var id = same_tags.indexOf(element) + 1;
			id > 1 ? (id = '[' + id + ']') : (id = '');
			xpath = '/' + element.tagName.toLowerCase() + id + xpath;
		}
		return xpath;
	} catch (e) {
		console.log("Error: " + e)
		return "";
	}
}

function isElementFullyVisible(element) {
    if (!element) return false;

    const style = window.getComputedStyle(element);

    if (style.display === "none" || style.visibility === "hidden" || style.opacity === "0") {
        return false;
    }

    const rect = element.getBoundingClientRect();
    const inViewport = (
        rect.width > 0 &&
        rect.height > 0 &&
        rect.top >= 0 &&
        rect.left >= 0 &&
        rect.bottom <= (window.innerHeight || document.documentElement.clientHeight) &&
        rect.right <= (window.innerWidth || document.documentElement.clientWidth)
    );

    return inViewport;
}

added_events = Array();
function addEventListenerWrapper(elem, args) {

  let page_title = document.title;

  let parent_html = elem.parentElement ? elem.parentElement.outerHTML : '';

  let sibling_html = [];
  let siblings = elem.parentElement ? elem.parentElement.children : [];
  for (let i = 0; i < siblings.length; i++) {
    if (siblings[i] !== elem) {
      sibling_html.push(siblings[i].outerHTML);
    }
  }

  let dom_context = {
    "current_node": elem.outerHTML,
    "parent_node": parent_html,
    "sibling_nodes": sibling_html,
    "page_title": page_title
  };

  let current_url = window.location.href;

  tag = elem.tagName
  dom_adress = "";
  id = elem.id;
  html_class = elem.className;
  dom_adress = getXPath(elem);

  if( !dom_adress ) {
    console.log("No dom_adress, using fake-id")
    elem.id = MD5(elem.outerHTML);
    dom_adress = '//*[@id="'+elem.id+'"]';
  }

	function_id = MD5(args[1].toString())
	resp = {
		"event" : args[0],
		"function_id" : function_id,
		"addr" : dom_adress,
		"id" : id,
		"tag" : tag,
		"class" : html_class,
		"dom_context": dom_context,
		"url": current_url,
		"is_visible": isElementFullyVisible(elem)
	}
  added_events.push( resp )
	if (args[0] == "change") {
		inputs = elem.querySelectorAll("input");
		selects = elem.querySelectorAll("select");
		options = elem.querySelectorAll("option");

		for (i = 0; i < inputs.length; i++) {
			e = inputs[i];
			if (e.getAttribute("type") == "radio"
					|| e.getAttribute("type") == "checkbox") {
				let input_dom_context = {
                  "current_node": e.outerHTML,
                  "parent_node": e.parentElement ? e.parentElement.outerHTML : '',
                  "sibling_nodes": Array.from(e.parentElement ? e.parentElement.children : []).map(sibling => sibling.outerHTML),
                  "page_title": document.title
                };

				tag = e.tagName
				id = e.id;
				html_class = e.className;
				dom_adress = getXPath(e);
				function_id = "";
				resp = {
					"event" : "change",
					"function_id" : function_id,
					"addr" : dom_adress,
					"id" : id,
					"tag" : tag,
					"class" : html_class,
					"dom_context": input_dom_context,
					"url": window.location.href,
					"is_visible": isElementFullyVisible(e)
				}
				resp = JSON.stringify(resp)
			}
		}
		for (i = 0; i < selects.length; i++) {
			s = selects[i];

            let select_dom_context = {
                "current_node": s.outerHTML,
                "parent_node": s.parentElement ? s.parentElement.outerHTML : '',
                "sibling_nodes": s.parentElement ? Array.from(s.parentElement.children).map(sibling => sibling.outerHTML) : [],
                "page_title": document.title
            };

			tag = s.tagName;
			id = s.id;
			html_class = s.className;
			dom_adress = getXPath(s);
			function_id = "";
			resp = {
				"event" : "change",
				"function_id" : function_id,
				"addr" : dom_adress,
				"id" : id,
				"tag" : tag,
				"class" : html_class,
				"dom_context": select_dom_context,
				"url": window.location.href,
				"is_visible": isElementFullyVisible(s)
			}
			resp = JSON.stringify(resp)
		}
		for (xx = 0; xx < options.length; xx++) {
			element = options[i]

			let option_dom_context = {
			    "current_node": element.outerHTML,
                "parent_node": element.parentElement ? element.parentElement.outerHTML : '',
                "sibling_nodes": element.parentElement ?Array.from(element.parentElement.children).map(sibling => sibling.outerHTML) : [],
                "page_title": document.title
            };

			tag = element.tagName
			id = element.id;
			html_class = element.className;
			dom_adress = getXPath(element);
			function_id = "";
			resp = {
				"event" : "change",
				"function_id" : function_id,
				"addr" : dom_adress,
				"id" : id,
				"tag" : tag,
				"class" : html_class,
				"dom_context": option_dom_context,
				"url": window.location.href,
				"is_visible": isElementFullyVisible(element)
			}
			resp = JSON.stringify(resp)
		}
	}
    if (tag == "TABLE" && args[0] == "click"){
        candidates = elem.querySelectorAll("button");
        for( xx = 0; xx < candidates.length; xx++) {
            var element = candidates[xx];

            let button_dom_context = {
                "current_node": element.outerHTML,
                "parent_node": element.parentElement ? element.parentElement.outerHTML : '',
                "sibling_nodes": element.parentElement ? Array.from(element.parentElement.children).map(sibling => sibling.outerHTML) : [],
                "page_title": document.title
            };

            tag = element.tagName;
            id = element.id;
            html_class = element.className;
            dom_adress = getXPath(element);
            function_id = "";
            resp = {
                "event": "click",
                "function_id": function_id,
                "addr": dom_adress,
                "id": id,
                "tag": tag,
                "class": html_class,
                "dom_context": button_dom_context,
                "url": window.location.href,
                "is_visible": isElementFullyVisible(element)
            };
            added_events.push( resp )
        };
    }
}

function bodyAddEventListenerWrapper(elem, args) {
	tag = "body"
	dom_adress = "";
	id = elem.id;
	html_class = elem.className;
	function_id = MD5(args[1].toString())
	dom_adress = "/html/body"
	resp = {
		"event" : args[0],
		"function_id" : function_id,
		"addr" : dom_adress,
		"id" : id,
		"tag" : tag,
		"class" : html_class
	}
    console.log(resp)
}

