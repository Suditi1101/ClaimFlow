import os
from pathlib import Path
from django.core.exceptions import ImproperlyConfigured
from config.database import database_config

BASE_DIR = Path(__file__).resolve().parent.parent
TESTING = os.getenv("TESTING") == "1"
DEBUG = os.getenv("DEBUG", "false") == "true"
SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "test-only" if TESTING else "")
if not SECRET_KEY:
    raise ImproperlyConfigured("DJANGO_SECRET_KEY is required")
ALLOWED_HOSTS = os.getenv("ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "rest_framework.authtoken",
    "django_prometheus",
    "common",
    "claims",
    "policies",
    "verification",
    "payments",
    "notifications",
]
MIDDLEWARE = [
    "django_prometheus.middleware.PrometheusBeforeMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "django_prometheus.middleware.PrometheusAfterMiddleware",
]
ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ]
        },
    }
]

DATABASES = {"default": database_config(os.environ)}
if TESTING and os.getenv("TEST_POSTGRES") != "1":
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "test.sqlite3",
        }
    }
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.TokenAuthentication",
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 50,
    "DEFAULT_THROTTLE_CLASSES": ["rest_framework.throttling.UserRateThrottle"],
    "DEFAULT_THROTTLE_RATES": {"user": "120/min"},
}
CACHES = {
    "default": {
        "BACKEND": "django_redis.cache.RedisCache",
        "LOCATION": os.getenv("REDIS_URL", "redis://localhost:6379/1"),
        "OPTIONS": {
            "CLIENT_CLASS": "django_redis.client.DefaultClient",
            "SOCKET_TIMEOUT": 2,
            "SOCKET_CONNECT_TIMEOUT": 2,
        },
    }
}
if TESTING:
    CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
STORAGES = {
    "default": {
        "BACKEND": "storages.backends.s3.S3Storage",
        "OPTIONS": {
            "bucket_name": os.getenv("S3_BUCKET", "claimflow"),
            "endpoint_url": os.getenv("S3_ENDPOINT", "http://localhost:9000"),
            "access_key": os.getenv("S3_ACCESS_KEY"),
            "secret_key": os.getenv("S3_SECRET_KEY"),
            "region_name": "us-east-1",
            "default_acl": None,
            "querystring_expire": 300,
            "file_overwrite": False,
        },
    },
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}
if TESTING:
    STORAGES["default"] = {"BACKEND": "django.core.files.storage.InMemoryStorage"}
KAFKA = {
    "bootstrap.servers": os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092"),
    "security.protocol": os.getenv("KAFKA_SECURITY_PROTOCOL", "PLAINTEXT"),
}
if os.getenv("KAFKA_SASL_USERNAME"):
    KAFKA.update(
        {
            "sasl.username": os.environ["KAFKA_SASL_USERNAME"],
            "sasl.password": os.environ["KAFKA_SASL_PASSWORD"],
            "sasl.mechanism": os.getenv("KAFKA_SASL_MECHANISM", "PLAIN"),
        }
    )
KAFKA_TOPIC = "claimflow.events.v1"
KAFKA_DLQ = "claimflow.dead-letter.v1"
TIME_ZONE = "UTC"
USE_TZ = True
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
DATA_UPLOAD_MAX_MEMORY_SIZE = 12 * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 1024 * 1024
SESSION_COOKIE_SECURE = CSRF_COOKIE_SECURE = not DEBUG and not TESTING
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
]
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": "INFO"},
}

FILE_UPLOAD_HANDLERS = [
    "common.upload_limits.MaxBytesUploadHandler",
    "django.core.files.uploadhandler.MemoryFileUploadHandler",
    "django.core.files.uploadhandler.TemporaryFileUploadHandler",
]

LOGIN_URL = "/accounts/login/"
LOGIN_REDIRECT_URL = "/accounts/"
LOGOUT_REDIRECT_URL = "/accounts/login/"
MAX_DOCUMENT_BYTES = 4 * 1024 * 1024 if os.getenv("VERCEL") else 10 * 1024 * 1024
SUPABASE_STORAGE_URL = os.getenv("CLAIM_SUPABASE_URL", "")
SUPABASE_STORAGE_KEY = os.getenv("CLAIM_SUPABASE_SERVICE_ROLE_KEY", "")
SUPABASE_STORAGE_BUCKET = "claimflow-documents"
if not TESTING and SUPABASE_STORAGE_URL and SUPABASE_STORAGE_KEY:
    STORAGES["default"] = {"BACKEND": "common.storage.SupabaseStorage"}
OWNER_SETUP_HASH = os.getenv("CLAIMFLOW_OWNER_SETUP_HASH", "")
OWNER_SETUP_EXPIRES = int(os.getenv("CLAIMFLOW_OWNER_SETUP_EXPIRES", "0"))
