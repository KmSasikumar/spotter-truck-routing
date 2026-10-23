from django.urls import path

from . import views

urlpatterns = [
    path("geocode/", views.geocode, name="geocode"),
    path("plan/", views.plan, name="plan"),
    path("heatmap/", views.heatmap, name="heatmap"),
]
