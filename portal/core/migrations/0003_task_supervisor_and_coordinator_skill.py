"""
Data migration for the Task Supervisor rollout.

TaskTypes are data, and `seed_real_data` only ever `get_or_create`s them, so it
would never update the rows a production install already has. This does it once:

* Event Coordinator no longer needs the "Coordination" skill (any active
  first-year can be one now).
* Vetter can no longer be added by hand — new Post requests have no vetting
  step. Requests already in flight that have a Vetter task keep it.
* The Task Supervisor role exists.

Skipped entirely on an empty TaskType table (a fresh install or a test
database) so it can't collide with rows created afterwards by the seed command
or by test fixtures.
"""

from django.db import migrations


def apply(apps, schema_editor):
    TaskType = apps.get_model("core", "TaskType")
    if not TaskType.objects.exists():
        return

    TaskType.objects.filter(task="Event Coordinator").update(required_skill="")
    TaskType.objects.filter(task="Vetter").update(internal_assignable=False)
    TaskType.objects.get_or_create(
        task="Task Supervisor",
        defaults={
            "required_skill": "",
            "points": 0,
            "sla_hours": 0,
            "at_event": False,
            "requestable": False,
            "internal_assignable": True,
            "vertical": "",
        },
    )


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0002_verticals_strikes_supervisor_subevents"),
    ]

    operations = [
        migrations.RunPython(apply, migrations.RunPython.noop),
    ]
