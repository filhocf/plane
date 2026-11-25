#!/usr/bin/env python3
"""Sequential, best-effort idempotent publisher for project Pages via Plane API v1.

Input JSON: {"name": "Root", "description_html": "<p>...</p>", "children": [...]}.
Requires PLANE_API_BASE (e.g. http://localhost:8001/api/v1), PLANE_API_KEY,
PLANE_WORKSPACE_SLUG and PLANE_PROJECT_ID. No session cookie is used.
"""

import json
import os
import sys
import urllib.error
import urllib.request

MAX_PAGE_DEPTH = 5


def request(method, url, payload=None):
    body = json.dumps(payload).encode() if payload is not None else None
    headers = {"X-Api-Key": os.environ["PLANE_API_KEY"]}
    if body is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=30) as response:
        return json.load(response)


def validate_tree(node, depth=0, seen=None):
    if seen is None:
        seen = set()
    if not isinstance(node, dict) or not isinstance(node.get("name"), str) or not node["name"].strip():
        raise ValueError("Each node needs a nonempty name")
    if not isinstance(node.get("children", []), list):
        raise ValueError("children must be a list")
    if depth > MAX_PAGE_DEPTH:
        raise ValueError(f"Nesting deeper than {MAX_PAGE_DEPTH} levels is not allowed")
    marker = id(node)
    if marker in seen:
        raise ValueError("The input tree contains a cycle")
    seen.add(marker)
    for child in node.get("children", []):
        validate_tree(child, depth + 1, seen)
    seen.remove(marker)


def list_all(url):
    items = []
    while url:
        response = request("GET", url)
        if isinstance(response, list):
            items.extend(response)
            break
        if not isinstance(response, dict) or not isinstance(response.get("results"), list):
            raise ValueError("Unexpected list response from Plane")
        items.extend(response["results"])
        url = response.get("next")
    return items


def _publish(base, node, parent=None):
    siblings = list_all(base if parent is None else f"{base}{parent}/children/")
    matches = [item for item in siblings if item["name"] == node["name"]]
    if len(matches) > 1:
        raise ValueError(f"Ambiguous duplicate title under parent {parent}: {node['name']}")
    if matches:
        page = matches[0]
        # Reusing a page deliberately does not PATCH title/content: live Yjs may be open.
        print(f"reuse {page['id']} {node['name']}")
    else:
        payload = {"name": node["name"]}
        if parent is not None:
            payload["parent"] = parent
        if "description_html" in node:
            payload["description_html"] = node["description_html"]
        page = request("POST", base, payload)
        print(f"create {page['id']} {node['name']}")
    for child in node.get("children", []):
        _publish(base, child, page["id"])
    return page["id"]


def publish(base, node, parent=None):
    validate_tree(node)
    return _publish(base, node, parent)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: publish_nested_pages.py tree.json")
    try:
        with open(sys.argv[1], encoding="utf-8") as source:
            tree = json.load(source)
        base = (
            os.environ["PLANE_API_BASE"].rstrip("/")
            + "/workspaces/"
            + os.environ["PLANE_WORKSPACE_SLUG"]
            + "/projects/"
            + os.environ["PLANE_PROJECT_ID"]
            + "/pages/"
        )
        publish(base, tree)
    except (KeyError, ValueError, OSError) as error:
        sys.exit(f"publish failed: {error}")
