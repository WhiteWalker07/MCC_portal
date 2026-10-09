"""
A requesting body's role chip says what it is (Club, Committee, SIG, Office), taken from the
body's own type, instead of "Committee" for everyone.
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from core.models import Committee
from engine.tests.factories import COMMITTEE_EMAIL, build_world

User = get_user_model()


class RoleBadgeTests(TestCase):
    def setUp(self):
        build_world()
        self.user = User.objects.create_user("club", email=COMMITTEE_EMAIL)
        self.client.force_login(self.user)

    def badges(self, name="home"):
        return self.client.get(reverse(name)).context["role_badges"]

    def set_type(self, value):
        Committee.objects.filter(email=COMMITTEE_EMAIL).update(type=value)

    def test_a_club_is_labelled_club(self):
        self.set_type("Club")
        self.assertEqual(self.badges(), ["Club"])

    def test_a_committee_sig_and_office_keep_their_own_label(self):
        for value in ("Committee", "SIG", "Office"):
            self.set_type(value)
            self.assertEqual(self.badges(), [value], value)

    def test_a_blank_type_falls_back_to_committee(self):
        self.set_type("")
        self.assertEqual(self.badges(), ["Committee"])

    def test_the_label_shows_on_home_and_on_the_profile(self):
        self.set_type("Club")
        self.assertContains(self.client.get(reverse("home")), '<span class="badge">Club</span>')
        self.assertContains(self.client.get(reverse("profile")), '<span class="badge">Club</span>')
        self.assertNotContains(self.client.get(reverse("home")), '<span class="badge">Committee</span>')

    def test_other_roles_are_unchanged(self):
        from engine.tests.factories import NEHA

        member = User.objects.create_user("neha", email=NEHA)
        self.client.force_login(member)
        self.assertIn("Team", self.badges())
