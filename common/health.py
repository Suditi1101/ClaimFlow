from django.http import JsonResponse
from django.db import connection
from django.core.cache import cache


def live(request):
    return JsonResponse({"status": "alive"})


def ready(request):
    try:
        with connection.cursor() as c:
            c.execute("SELECT 1")
        cache.set("claimflow:ready", 1, 10)
        if cache.get("claimflow:ready") != 1:
            raise RuntimeError()
    except Exception:
        return JsonResponse({"status": "unavailable"}, status=503)
    return JsonResponse({"status": "ready"})
