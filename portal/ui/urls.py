"""
Portal routes.

These mirror the hash routes the old SPA served (`web/js/shell.js`), so muscle
memory and any bookmarked links map across predictably:

    #/new          -> /requests/new/
    #/requests     -> /requests/
    #/tasks        -> /tasks/
    #/assignments  -> /assignments/
    #/approvals    -> /approvals/
    #/dashboard    -> /dashboard/
    #/admin        -> /portal-admin/
"""

from django.urls import path

from . import views

urlpatterns = [
    path("", views.home, name="home"),
    path("signed-out/", views.signed_out, name="signed-out"),

    path("requests/new/", views.request_new, name="request-new"),
    path("requests/", views.request_list, name="request-list"),
    path("requests/<int:pk>/", views.request_detail, name="request-detail"),
    path("requests/<int:pk>/venue/", views.request_edit_venue, name="request-edit-venue"),

    path("tasks/", views.task_list, name="task-list"),
    path("tasks/<int:pk>/done/", views.task_complete, name="task-complete"),

    path("assignments/", views.assignment_list, name="assignment-list"),
    path("assignments/strike/", views.issue_strike, name="issue-strike"),
    path("assignments/<int:pk>/", views.assignment_detail, name="assignment-detail"),
    path("assignments/<int:pk>/reassign/", views.assignment_reassign, name="assignment-reassign"),
    path("assignments/<int:request_pk>/add/", views.assignment_add, name="assignment-add"),
    path("assignments/<int:request_pk>/ready/", views.mark_ready_to_post, name="mark-ready"),

    path("approvals/", views.approval_list, name="approval-list"),
    path("approvals/<int:pk>/", views.approval_detail, name="approval-detail"),
    path("approvals/<int:pk>/decide/", views.approval_decide, name="approval-decide"),

    path("dashboard/", views.dashboard, name="dashboard"),

    path("portal-admin/", views.portal_admin, name="portal-admin"),
    path("portal-admin/team-import/", views.team_import, name="team-import"),
    path("portal-admin/vertical-head/", views.set_vertical_head, name="vertical-head"),
    path("portal-admin/availability/", views.set_availability, name="set-availability"),
    path("portal-admin/points/", views.point_scheme, name="point-scheme"),
    path("portal-admin/committees/", views.committee_manage, name="committee-manage"),
    path("portal-admin/remove-strike/", views.remove_strike, name="remove-strike"),
    path("portal-admin/remove-from-team/", views.remove_from_team, name="remove-from-team"),
]
