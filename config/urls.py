from django.contrib.auth.views import LogoutView
from django.views.generic import RedirectView
from common.portal import PortalLoginView, signup, setup_owner, dashboard
from django.contrib import admin
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from claims.api import ClaimViewSet, DocumentViewSet
from common.health import live, ready

router = DefaultRouter(trailing_slash=False)
router.register("claims", ClaimViewSet, basename="claim")
router.register("documents", DocumentViewSet, basename="document")
urlpatterns = [
    path("", RedirectView.as_view(url="/accounts/", permanent=False), name="home"),
    path("accounts/", dashboard, name="portal"),
    path("accounts/login/", PortalLoginView.as_view(), name="login"),
    path("accounts/logout/", LogoutView.as_view(), name="logout"),
    path("accounts/signup/", signup, name="signup"),
    path("accounts/setup/", setup_owner, name="owner-setup"),
    path("api/", include(router.urls)),
    path("admin/", admin.site.urls),
    path("health/live", live),
    path("health/ready", ready),
    path("", include("django_prometheus.urls")),
]
