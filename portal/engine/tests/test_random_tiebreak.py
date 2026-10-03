"""
Automatic assignment picks at random, as a last resort, among candidates that are
still tied after vertical tier and points. These tests switch the randomness ON
(the rest of the suite runs with it off so it stays deterministic) and check the
two halves of the rule: it really is random among equals, and it is never random
at the expense of a better candidate.
"""

from __future__ import annotations

import random
from datetime import timedelta
from unittest import mock

from django.test import TestCase, override_settings

from core.config import get_settings, get_team
from core.models import TeamMember
from engine import meetings as meetings_engine
from engine.assign import choose_member, choose_supervisor, eligible_members, pick_best
from engine.pipeline import PipelineTask
from engine.tests.factories import ASHA, NEHA, FreeCalendar, build_world, coverage_request
from engine.tests.test_meetings_and_pairing import member
from ui.views import _resolve_member

RANDOM_ON = override_settings(PORTAL_RANDOM_TIE_BREAK=True)
TRIALS = 300


def photographer_task(vertical="Photography"):
    return PipelineTask(
        task="Photographer", required_skill="Photography", points=10, sla_hours=0,
        at_event=True, vertical=vertical, deadline=None,
    )


class Base(TestCase):
    def setUp(self):
        build_world()
        self.request = coverage_request(starts_in=timedelta(days=5))
        TeamMember.objects.filter(email=ASHA).update(points=500)  # keep the fixture's Asha out of the way
        self.p1 = member("p1@i.ac.in", "Pia", vertical="Photography", skills=["Photography"])
        self.p2 = member("p2@i.ac.in", "Zed", vertical="Photography", skills=["Photography"])
        self.p3 = member("p3@i.ac.in", "Mo", vertical="Photography", skills=["Photography"])

    def picks(self, trials=TRIALS, **kwargs):
        return [
            choose_member(
                photographer_task(), self.request, get_settings(), get_team(), set(), FreeCalendar()
            ).member.name
            for _ in range(trials)
        ]


class RandomAmongEqualsTests(Base):
    @RANDOM_ON
    def test_everyone_tied_gets_picked_sometimes_not_just_the_first_alphabetically(self):
        names = set(self.picks())
        self.assertEqual(names, {"Mo", "Pia", "Zed"})  # not only "Mo", the alphabetical first

    @RANDOM_ON
    def test_it_is_not_a_fixed_alternation_either(self):
        picks = self.picks(60)
        self.assertGreater(len(set(picks)), 1)
        self.assertNotEqual(picks, sorted(picks))  # not alphabetical order of repeated picks

    @RANDOM_ON
    def test_the_random_path_really_is_what_decides_it(self):
        with mock.patch("engine.assign.random.choice", side_effect=lambda items: items[-1]) as chooser:
            pick = choose_member(photographer_task(), self.request, get_settings(), get_team(), set(), FreeCalendar())
        chooser.assert_called_once()
        self.assertEqual(pick.member.name, "Zed")  # the last of the tied, proving it isn't items[0]

    @override_settings(PORTAL_RANDOM_TIE_BREAK=False)
    def test_with_it_switched_off_the_first_alphabetically_is_picked(self):
        self.assertEqual(set(self.picks(20)), {"Mo"})

    def test_a_seeded_generator_makes_it_reproducible(self):
        with override_settings(PORTAL_RANDOM_TIE_BREAK=True):
            random.seed(7)
            first = self.picks(25)
            random.seed(7)
            second = self.picks(25)
        self.assertEqual(first, second)


class NeverAtTheExpenseOfABetterCandidateTests(Base):
    @RANDOM_ON
    def test_fewer_points_always_wins_over_a_tie_lower_down(self):
        TeamMember.objects.filter(pk=self.p1.pk).update(points=5)  # Pia is the only one with the fewest
        TeamMember.objects.filter(pk__in=[self.p2.pk, self.p3.pk]).update(points=9)
        self.assertEqual(set(self.picks()), {"Pia"})

    @RANDOM_ON
    def test_only_those_tied_on_the_lowest_points_are_ever_picked(self):
        TeamMember.objects.filter(pk__in=[self.p1.pk, self.p3.pk]).update(points=5)
        TeamMember.objects.filter(pk=self.p2.pk).update(points=9)
        self.assertEqual(set(self.picks()), {"Pia", "Mo"})

    @RANDOM_ON
    def test_the_primary_vertical_beats_the_secondary_whatever_the_points(self):
        TeamMember.objects.filter(pk__in=[self.p1.pk, self.p3.pk]).update(
            vertical="Videography", secondary_vertical="Photography", points=0
        )
        TeamMember.objects.filter(pk=self.p2.pk).update(points=40)  # Zed: primary Photography but busy
        self.assertEqual(set(self.picks()), {"Zed"})

    @RANDOM_ON
    def test_someone_already_on_the_request_is_not_picked_while_others_are_free(self):
        for _ in range(TRIALS):
            pick = choose_member(
                photographer_task(), self.request, get_settings(), get_team(), {self.p1.email}, FreeCalendar()
            )
            self.assertNotEqual(pick.member.email, self.p1.email)

    @RANDOM_ON
    def test_a_single_candidate_is_simply_chosen_and_nobody_gives_a_clean_none(self):
        self.assertEqual(pick_best([self.p1]), self.p1)
        self.assertIsNone(pick_best([]))


class ListsStayReadableTests(Base):
    @RANDOM_ON
    def test_the_dropdown_order_is_still_alphabetical_and_stable(self):
        def listed():
            return [
                m.name
                for m in eligible_members(
                    "Photography", True, self.request, get_settings(), get_team(), FreeCalendar(),
                    task_name="Photographer", vertical="Photography",
                )
            ]

        first = listed()
        self.assertEqual(first[:3], ["Mo", "Pia", "Zed"])
        self.assertTrue(all(listed() == first for _ in range(20)))


class OtherAutoPicksTests(Base):
    @RANDOM_ON
    def test_the_supervisor_pick_is_random_among_equal_workloads_and_never_beats_a_lighter_one(self):
        s1 = member("s1@i.ac.in", "Sue", year=2)
        s2 = member("s2@i.ac.in", "Tom", year=2)
        s3 = member("s3@i.ac.in", "Una", year=2)
        sup = TeamMember.objects.get(email="supervisor@iimsirmaur.ac.in")
        team = [sup, s1, s2, s3]
        equal = {choose_supervisor(self.request, get_settings(), team, {}).member.name for _ in range(TRIALS)}
        self.assertEqual(equal, {"Supervisor", "Sue", "Tom", "Una"})
        busy = {sup.email: 3, s1.email: 3, s3.email: 3}
        lighter = {choose_supervisor(self.request, get_settings(), team, busy).member.name for _ in range(TRIALS)}
        self.assertEqual(lighter, {"Tom"})  # fewest open supervisions always wins

    @RANDOM_ON
    def test_the_minutes_taker_is_random_among_tied_first_years_and_never_a_second_year(self):
        second_year = member("y2@i.ac.in", "Yves", year=2, points=-10)
        invitees = [self.p1, self.p2, self.p3, second_year]
        picks = {meetings_engine.choose_mom(invitees).name for _ in range(TRIALS)}
        self.assertEqual(picks, {"Pia", "Zed", "Mo"})

    @RANDOM_ON
    def test_the_minutes_taker_with_fewer_points_always_wins(self):
        TeamMember.objects.filter(pk=self.p2.pk).update(points=-3)
        self.p2.refresh_from_db()
        picks = {meetings_engine.choose_mom([self.p1, self.p2, self.p3]).name for _ in range(TRIALS)}
        self.assertEqual(picks, {"Zed"})

    @RANDOM_ON
    def test_the_hand_assign_screens_auto_pick_is_random_too(self):
        from ui.forms import ReassignForm

        class AutoForm:
            mode = ReassignForm.MODE_AUTO

        pool = eligible_members(
            "Photography", True, self.request, get_settings(), get_team(), FreeCalendar(),
            task_name="Photographer", vertical="Photography",
        )
        names = {
            _resolve_member(AutoForm(), pool, "Photography", True, self.request, get_settings(),
                            task_name="Photographer", vertical="Photography").name
            for _ in range(TRIALS)
        }
        self.assertEqual(names, {"Mo", "Pia", "Zed"})
