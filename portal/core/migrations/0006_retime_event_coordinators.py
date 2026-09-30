"""
Data migration for the timed Event Coordinator.

The coordinator used to be due at the event end and was never swept for
lateness. It is now due 12 hours after the request's last other individual task
(`engine.pipeline.coordinator_deadline`), is swept like any other task, and a
coordinator on a Post request has no deadline. Coordinators already in flight
still carry the old deadline, so without this the first hourly sweep after
deploying would mark every one whose event has ended LATE.

A finished coordinator keeps the deadline it was judged against. The grace
hours are written out here rather than imported so this migration keeps doing
the same thing if the constant later changes.
"""

from datetime import timedelta

from django.db import migrations

GRACE_HOURS = 12
COORDINATOR = "Event Coordinator"
SUPERVISOR = "Task Supervisor"


def apply(apps, schema_editor):
    Task = apps.get_model("core", "Task")

    open_coordinators = Task.objects.filter(task=COORDINATOR).exclude(status="DONE").select_related("request")
    for coordinator in open_coordinators:
        request_obj = coordinator.request
        if request_obj.type != "Coverage":
            new_deadline = None
        else:
            others = [
                d
                for d in Task.objects.filter(request=request_obj)
                .exclude(task__in=[COORDINATOR, SUPERVISOR])
                .values_list("deadline", flat=True)
                if d is not None
            ]
            latest = max(others) if others else request_obj.event_end
            new_deadline = latest + timedelta(hours=GRACE_HOURS) if latest else None
        if coordinator.deadline != new_deadline:
            coordinator.deadline = new_deadline
            coordinator.save(update_fields=["deadline"])


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0005_leave_requests"),
    ]

    operations = [
        migrations.RunPython(apply, migrations.RunPython.noop),
    ]
