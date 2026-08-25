"""
Django admin registration.

This is where the "straight CRUD" half of the old Admin view lives now (the
plan's §6 split): committees, roster, task types, platforms, slots and the
point scheme are all plain record editing, so Django admin gives it to us with
validation, search and an audit trail for free. `/portal-admin/` (ui/views.py)
keeps only the three things that are workflow actions rather than record
edits — CSV import, vertical-head assignment, and the availability toggle.

Access here follows `is_staff` / `is_superuser`, which accounts/adapters.py
keeps in sync with PortalSettings.secretary_emails / admin_emails on every
sign-in — so who can reach this page is still data, not code
(docs/PRD.md §4).
"""

from __future__ import annotations

from django.contrib import admin

from .models import (
    ActivityLog,
    Committee,
    PointsScheme,
    PortalSettings,
    Platform,
    PostSlot,
    Request,
    Task,
    TaskType,
    TeamMember,
)


@admin.register(PortalSettings)
class PortalSettingsAdmin(admin.ModelAdmin):
    """The single most sensitive record in the system — who has admin/secretary."""

    list_display = ("__str__", "sla_hours", "strike_limit", "campus_strict")

    def has_add_permission(self, request):
        return False  # singleton — created lazily by PortalSettings.load()

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(PointsScheme)
class PointsSchemeAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(TaskType)
class TaskTypeAdmin(admin.ModelAdmin):
    list_display = ("task", "required_skill", "vertical", "points", "sla_hours", "at_event", "requestable", "internal_assignable")
    list_filter = ("vertical", "at_event", "requestable", "internal_assignable")
    search_fields = ("task", "required_skill")


@admin.register(PostSlot)
class PostSlotAdmin(admin.ModelAdmin):
    list_display = ("time",)


@admin.register(Platform)
class PlatformAdmin(admin.ModelAdmin):
    list_display = ("platform", "handler_email", "points", "active")
    list_filter = ("platform", "active")
    search_fields = ("platform", "handler_email")


@admin.register(Committee)
class CommitteeAdmin(admin.ModelAdmin):
    list_display = ("name", "acronym", "type", "campus", "email", "last_seq")
    list_filter = ("type", "campus")
    search_fields = ("name", "acronym", "email")
    readonly_fields = ("last_seq",)  # engine-owned counter — visible, not editable


@admin.register(TeamMember)
class TeamMemberAdmin(admin.ModelAdmin):
    list_display = ("name", "email", "vertical", "campus", "year", "active", "points", "strikes", "availability", "domain_head_of")
    list_filter = ("vertical", "campus", "year", "active", "availability")
    search_fields = ("name", "email")
    readonly_fields = ("availability_changed_at", "on_work_days", "out_days")


class TaskInline(admin.TabularInline):
    model = Task
    extra = 0
    fields = ("task", "member", "email", "status", "deadline", "points")
    readonly_fields = ("task", "member", "email", "status", "deadline", "points")
    can_delete = False
    show_change_link = True

    def has_add_permission(self, request, obj=None):
        return False  # tasks are only ever created by the engine


@admin.register(Request)
class RequestAdmin(admin.ModelAdmin):
    list_display = ("ref_code", "type", "event_name", "status", "contact_email", "campus", "created_at")
    list_filter = ("type", "status", "campus")
    search_fields = ("ref_code", "event_name", "contact_email", "coordinator_email")
    date_hierarchy = "created_at"
    inlines = [TaskInline]
    readonly_fields = ("ref_code", "campus", "coordinator_email", "roster", "posts", "created_at")


@admin.register(Task)
class TaskAdmin(admin.ModelAdmin):
    list_display = ("ref_code", "task", "member", "status", "deadline", "points", "vertical")
    list_filter = ("status", "task", "vertical", "req_type")
    search_fields = ("ref_code", "member", "email")
    readonly_fields = ("points_awarded", "timing_applied", "struck")


@admin.register(ActivityLog)
class ActivityLogAdmin(admin.ModelAdmin):
    list_display = ("timestamp", "event", "ref_code", "actor", "member", "detail")
    list_filter = ("event",)
    search_fields = ("ref_code", "actor", "member", "detail")
    date_hierarchy = "timestamp"

    def has_add_permission(self, request):
        return False  # append-only, written by the engine

    def has_change_permission(self, request, obj=None):
        return False
