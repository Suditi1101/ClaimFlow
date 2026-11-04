from django.contrib import admin
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from claims.api import ClaimViewSet, DocumentViewSet
from common.health import live, ready

router = DefaultRouter(trailing_slash=False)
router.register("claims", ClaimViewSet, basename="claim")
router.register("documents", DocumentViewSet, basename="document")
urlpatterns = [
    path("api/", include(router.urls)),
    path("admin/", admin.site.urls),
    path("health/live", live),
    path("health/ready", ready),
    path("", include("django_prometheus.urls")),
]
