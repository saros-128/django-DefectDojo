from importlib import import_module

from django.conf import settings
from django.urls import path

from auditor_bridge import ui, views

dojo_urlpatterns = import_module(getattr(settings, "AUDITOR_BRIDGE_BASE_URLCONF", "dojo.urls")).urlpatterns

urlpatterns = [
    path("auditor/profiles/", ui.profiles, name="auditor_bridge_profiles"),
    path("api/v2/auditor/catalog/", views.CatalogView.as_view()),
    path("api/v2/auditor/profiles/", views.ProfileListView.as_view()),
    path("api/v2/auditor/profiles/<slug:key>/", views.ProfilesView.as_view()),
    path("api/v2/auditor/profiles/<slug:key>/sync/", views.ProfileSyncView.as_view()),
    path("api/v2/auditor/profiles/<slug:key>/disable/", views.ProfileDisableView.as_view()),
    path("api/v2/auditor/repositories/", views.InventoryView.as_view()),
    path("api/v2/auditor/repositories/<int:repository_pk>/binding/", views.RepositoryBindingView.as_view()),
    path("api/v2/auditor/imports/", views.ImportsView.as_view()),
    path("api/v2/auditor/callback/", views.CallbackView.as_view()),
    *dojo_urlpatterns,
]
