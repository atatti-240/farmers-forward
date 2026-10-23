"""Every /api route that takes a body answers a non-object body with a clean 400.

The routes read their body as a JSON object (`request.get_json(...) or {}`,
then `.get(...)`). A body that was valid JSON but not an object -- a list, a
string, a number -- used to reach that `.get` and come back as a 500 from
/api/submit, /api/score, the report download and the public /api/contact.
app.require_json_object_body() now refuses such bodies before any route runs.

The routes are discovered from the URL map rather than listed, so an endpoint
added later (say under /api/public/) is covered without touching this file. The
check runs before any login check, so none of these requests needs a session.
"""

import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _appfixture import app  # noqa: E402
from backend.app import JSON_OBJECT_REQUIRED  # noqa: E402

BODY_METHODS = ("POST", "PUT", "PATCH")

# Bodies that are not a JSON object, as (label, request kwargs).
NOT_AN_OBJECT = [
    ("a JSON list", {"json": ["x"]}),
    ("a JSON string", {"json": "x"}),
    ("a JSON number", {"json": 7}),
    ("JSON true", {"json": True}),
    ("JSON null", {"data": "null", "content_type": "application/json"}),
    ("malformed JSON", {"data": "{not json", "content_type": "application/json"}),
    ("a form post", {"data": {"name": "Ada"}}),
    ("plain text", {"data": "hello", "content_type": "text/plain"}),
]


def body_routes():
    """(method, concrete URL, rule) for every /api route that accepts a body."""
    routes = []
    for rule in app.url_map.iter_rules():
        if not str(rule).startswith("/api/"):
            continue
        # Fill each <converter:name> with a value the converter accepts.
        url = re.sub(r"<int:[^>]+>", "1", str(rule))
        url = re.sub(r"<[^>]+>", "x", url)
        for method in sorted(set(rule.methods) & set(BODY_METHODS)):
            routes.append((method, url, str(rule)))
    return routes


class JsonObjectBodyTests(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()

    def test_the_routes_that_used_to_500_are_all_covered(self):
        rules = {rule for _, _, rule in body_routes()}
        for rule in ("/api/contact", "/api/submit", "/api/score",
                     "/api/report/<int:farm_id>/all"):
            self.assertIn(rule, rules)

    def test_a_body_that_is_not_a_json_object_gets_a_clean_400(self):
        for method, url, _ in body_routes():
            for label, kwargs in NOT_AN_OBJECT:
                with self.subTest(method=method, url=url, body=label):
                    response = self.client.open(url, method=method, **kwargs)
                    self.assertEqual(response.status_code, 400)
                    self.assertEqual(response.get_json(), {"error": JSON_OBJECT_REQUIRED})

    def test_an_empty_json_object_is_never_a_500(self):
        """The route's own answer (401, 400, 200...) still comes through."""
        for method, url, _ in body_routes():
            with self.subTest(method=method, url=url):
                self.assertLess(self.client.open(url, method=method, json={}).status_code, 500)

    def test_json_objects_still_reach_the_routes(self):
        ok = self.client.post("/api/contact", json={
            "name": "Ada", "email": "ada@example.com", "message": "Hello"})
        self.assertEqual((ok.status_code, ok.get_json()), (200, {"ok": True}))
        # The route's own validation, not the body check, answers a missing field.
        missing = self.client.post("/api/contact", json={"name": "Ada"})
        self.assertEqual(missing.status_code, 400)
        self.assertNotEqual(missing.get_json(), {"error": JSON_OBJECT_REQUIRED})
        wrong = self.client.post("/api/auth/verify-code", json={"code": "nope"})
        self.assertEqual(wrong.get_json(), {"ok": False})

    def test_requests_without_a_body_are_left_alone(self):
        self.assertEqual(self.client.post("/api/auth/logout").get_json(), {"ok": True})
        # Not 400: the report download's map image is optional, so an empty
        # POST goes on to the route (which wants a signed-in admin).
        self.assertEqual(self.client.post("/api/report/1/all").status_code, 401)
        self.assertEqual(self.client.get("/api/auth/me").status_code, 200)


if __name__ == "__main__":
    unittest.main()
