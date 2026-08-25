"""
Root URL configuration.

Three groups:
  /admin/      Django admin — raw CRUD over committees, roster and engine config,
               plus the break-glass superuser login when Google is unreachable.
  /accounts/   allauth: the Google handshake and sign-out.
  everything   the portal itself (see ui/urls.py).
"""

from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/", include("allauth.urls")),
    path("", include("ui.urls")),
]

admin.site.site_header = "MCC Portal administration"
admin.site.site_title = "MCC Portal"
admin.site.index_title = "Portal data and configuration"
