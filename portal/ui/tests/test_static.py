"""
The stylesheet the browser gets must be the one in `static/`, not a stale copy that an
earlier `collectstatic` left in `staticfiles/` (a forgotten deploy step once left new
templates styled by an old stylesheet).
"""

from __future__ import annotations

from django.conf import settings
from django.test import TestCase


class StaticServingTests(TestCase):
    def fetch(self, path):
        response = self.client.get(f"/static/{path}")
        return response, b"".join(response.streaming_content) if response.streaming else response.content

    def test_the_stylesheet_served_is_the_one_in_the_static_folder(self):
        response, body = self.fetch("styles.css")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(body, (settings.BASE_DIR / "static" / "styles.css").read_bytes())

    def test_the_stylesheet_has_the_new_design_sidebar(self):
        _, body = self.fetch("styles.css")
        self.assertIn(b".side {", body)
        self.assertIn(b"Press Desk", body)

    def test_other_static_files_are_still_served(self):
        response, _ = self.fetch("favicon.svg")
        self.assertEqual(response.status_code, 200)
