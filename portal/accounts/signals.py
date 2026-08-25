"""
Re-sync admin/secretary rights on every sign-in.

`save_user` only fires the first time an account appears. Roles change over
time — a new secretary is appointed, a term ends — so the flags are refreshed on
each login too, making a role change take effect at the person's next sign-in
without anyone touching Django admin.
"""

from django.contrib.auth.signals import user_logged_in
from django.dispatch import receiver

from .adapters import _sync_staff_flags


@receiver(user_logged_in)
def sync_staff_flags_on_login(sender, request, user, **kwargs):
    _sync_staff_flags(user)
