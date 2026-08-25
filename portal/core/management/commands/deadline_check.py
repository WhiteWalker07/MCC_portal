"""
Hourly deadline sweep (docs/PRD.md §5.8).

Replaces the GitHub Actions cron that used to POST to `/api/cron/deadline-check`
on Render. On the lab PC this is a Windows Task Scheduler job — which is
strictly better here: it survives an app restart, keeps its own run history, and
needs no shared secret because it isn't reachable over the network at all.

    schtasks /Create /TN "MCC Portal deadline check" /SC HOURLY /RU SYSTEM ^
      /TR "C:\\mcc\\portal\\.venv\\Scripts\\python.exe C:\\mcc\\portal\\manage.py deadline_check"
"""

from django.core.management.base import BaseCommand

from engine.workflow import run_deadline_check


class Command(BaseCommand):
    help = "Mark overdue confirmed tasks LATE and issue the resulting strikes."

    def handle(self, *args, **options):
        result = run_deadline_check()
        late = result["late"]
        if late:
            self.stdout.write(self.style.WARNING(f"Marked {late} task(s) LATE."))
        else:
            self.stdout.write("Nothing overdue.")
