/*
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
 */

function isElementFullyVisible(element) {
    if (!element) return false;

    const style = window.getComputedStyle(element);

    // 检查 display, visibility, opacity
    if (style.display === "none" || style.visibility === "hidden" || style.opacity === "0") {
        return false;
    }

    // 检查是否在视口内
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

function catch_properties(){

  resps = []
  var elems = document.getElementsByTagName('*')
  // console.log(elems.length + " elems found...")
  for (my_counter_i = 0; my_counter_i < elems.length; my_counter_i++) {
    events = []
    tag = elems[my_counter_i].tagName
    dom_address = ""
    id = elems[my_counter_i].id
    if (elems[my_counter_i].onclick != null) {
      events.push({"method": "onclick", "func": elems[my_counter_i].onclick})
    }
    if (elems[my_counter_i].onmouseover != null) {
      events.push({"method": "onmouseover", "func": elems[my_counter_i].onmouseover})
    }
    if (elems[my_counter_i].onabort != null) {
      events.push({"method": "onabort", "func": elems[my_counter_i].onabort})
    }
    if (elems[my_counter_i].onblur != null) {
      events.push({"method": "onblur", "func": elems[my_counter_i].onblur})
    }
    if (elems[my_counter_i].onchange != null) {
      events.push({"method": "onchange", "func": elems[my_counter_i].onchange})
    }
    if (elems[my_counter_i].oninput != null) {
      events.push({"method": "oninput", "func": elems[my_counter_i].oninput})
    }
    if (elems[my_counter_i].ondblclick != null) {
      events.push({"method": "ondblclick", "func": elems[my_counter_i].ondblclick})
    }
    if (elems[my_counter_i].onerror != null) {
      events.push({"method": "onerror", "func": elems[my_counter_i].onerror})
    }
    if (elems[my_counter_i].onfocus != null) {
      events.push({"method": "onfocus", "func": elems[my_counter_i].onfocus})
    }
    if (elems[my_counter_i].onkeydown != null) {
      events.push({"method": "onkeydown", "func": elems[my_counter_i].onkeydown})
    }
    if (elems[my_counter_i].onkeypress != null) {
      events.push({"method": "onkeypress", "func": elems[my_counter_i].onkeypress})
    }
    if (elems[my_counter_i].onkeyup != null) {
      events.push({"method": "onkeyup", "func": elems[my_counter_i].onkeyup})
    }
    if (elems[my_counter_i].onmousedown != null) {
      events.push({"method": "onmousedown", "func": elems[my_counter_i].onmousedown})
    }
    if (elems[my_counter_i].onmousemove != null) {
      events.push({"method": "onmousemove", "func": elems[my_counter_i].onmousemove})
    }
    if (elems[my_counter_i].onmouseout != null) {
      events.push({"method": "onmouseout", "func": elems[my_counter_i].onmouseout})
    }
    if (elems[my_counter_i].onmouseup != null) {
      events.push({"method": "onmouseup", "func": elems[my_counter_i].onmouseup})
    }
    //console.log("We have: " + events.length + " events");
    if (events.length > 0) {
      elem = elems[my_counter_i]
      dom_adress = getXPath(elem);
      html_class = elems[my_counter_i].className;
      var parent_html = elem.parentElement ? elem.parentElement.outerHTML : '';
      var sibling_html = Array.from(elem.parentElement ? elem.parentElement.children : []).map(e => e.outerHTML).join();
      var page_title = document.title;

      console.log("parent_html: " + parent_html);
      console.log("url: " + window.location.href);

      for (my_counter_j = 0; my_counter_j < events.length; my_counter_j++) {
        //function_id = MD5(events[my_counter_j].func.toString() )
        function_id = MD5(events[my_counter_j].func.toString() + dom_adress )

        f = events[my_counter_j].func.toString()
        e = events[my_counter_j].event_type
        //clickable = JSON.parse(events[j])
        tut1 = events[my_counter_j];
        resp = {
          "function_id" : function_id,
          "event" : events[my_counter_j].method,
          "func" : events[my_counter_j].func.toString(),
          "id" : id,
          "tag" : tag,
          "addr" : dom_adress,
          "class" : html_class,
          "dom_context": {
            "current_node": elem.outerHTML,
            "parent_node": parent_html,
            "sibling_nodes": sibling_html,
            "page_title": page_title
          },
          "url": window.location.href,
          "is_visible": isElementFullyVisible(elem)
        }
        resps.push(resp);
      }
    }

  }
  return JSON.stringify(resps);
}

