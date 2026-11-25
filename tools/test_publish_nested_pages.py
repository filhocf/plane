"""Offline contract tests for the sequential API-key publisher."""

import unittest
from unittest.mock import patch

from publish_nested_pages import list_all, publish


class PublishNestedPagesTests(unittest.TestCase):
    def test_first_run_creates_and_second_reuses_same_tree(self):
        pages = []

        def fake_request(method, url, payload=None):
            if method == "GET":
                if url.endswith("/children/"):
                    parent = url.removesuffix("/children/").split("/")[-1]
                    return [p for p in pages if p.get("parent") == parent]
                return [p for p in pages if "parent" not in p]
            result = {**payload, "id": str(len(pages) + 1)}
            pages.append(result)
            return result

        tree = {"name": "Root", "children": [{"name": "Child"}]}
        with patch("publish_nested_pages.request", side_effect=fake_request) as api:
            self.assertEqual(publish("http://plane/pages/", tree), "1")
            self.assertEqual(publish("http://plane/pages/", tree), "1")
            self.assertEqual([p["name"] for p in pages], ["Root", "Child"])
            self.assertEqual([call.args[0] for call in api.call_args_list].count("POST"), 2)

    def test_ambiguous_duplicate_title_stops(self):
        with patch("publish_nested_pages.request", return_value=[
            {"name": "Root", "id": "1"}, {"name": "Root", "id": "2"},
        ]) as api, self.assertRaisesRegex(ValueError, "Ambiguous"):
            publish("http://plane/pages/", {"name": "Root"})
        api.assert_called_once()

    def test_invalid_children_stops_before_post(self):
        with patch("publish_nested_pages.request") as api, self.assertRaisesRegex(ValueError, "children"):
            publish("http://plane/pages/", {"name": "Root", "children": {}})
        api.assert_not_called()

    def test_paginated_siblings_are_all_searched(self):
        first = {"results": [{"name": "Other", "id": "1"}], "next": "page-2"}
        second = {"results": [{"name": "Root", "id": "2"}], "next": None}
        with patch("publish_nested_pages.request", side_effect=[first, second]) as api:
            self.assertEqual(publish("http://plane/pages/", {"name": "Root"}), "2")
        self.assertEqual([call.args[0] for call in api.call_args_list], ["GET", "GET"])

    def test_invalid_deep_tree_stops_before_any_request(self):
        tree = {"name": "0"}
        node = tree
        for depth in range(1, 7):
            child = {"name": str(depth)}
            node["children"] = [child]
            node = child
        with patch("publish_nested_pages.request") as api, self.assertRaisesRegex(ValueError, "deeper"):
            publish("http://plane/pages/", tree)
        api.assert_not_called()

    def test_unexpected_list_envelope_is_rejected(self):
        with patch("publish_nested_pages.request", return_value={"data": []}), self.assertRaisesRegex(
            ValueError, "Unexpected"
        ):
            list_all("http://plane/pages/")


if __name__ == "__main__":
    unittest.main()
