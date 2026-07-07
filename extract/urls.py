from django.urls import path
from . import views

urlpatterns = [
    path("", views.upload_view, name="upload"),
    path("result/<uuid:pk>/", views.result_view, name="result"),
    path("history/", views.history_view, name="history"),
    path("dashboard/", views.dashboard_view, name="dashboard"),
    path("compare/", views.compare_view, name="compare"),
    path("appeal/<uuid:pk>/", views.appeal_view, name="appeal"),
    path("guidelines/", views.guidelines_view, name="guidelines"),
    path("api/status/<uuid:pk>/", views.api_status, name="api_status"),
    path("api/stats/", views.api_stats, name="api_stats"),
]
