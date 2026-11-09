from django.http import JsonResponse
from django.db import connection
from django.core.cache import cache


def live(request):
    return JsonResponse({"status": "alive"})


def ready(request):
    checks = {"database": "unavailable", "cache": "unavailable"}
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
        checks["database"] = "ready"
    except Exception:
        pass
    try:
        cache.set("claimflow:ready", 1, 10)
        if cache.get("claimflow:ready") == 1:
            checks["cache"] = "ready"
    except Exception:
        pass
    available = all(value == "ready" for value in checks.values())
    return JsonResponse(
        {"status": "ready" if available else "unavailable", "checks": checks},
        status=200 if available else 503,
    )
