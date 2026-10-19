"""
Django settings for Examina project.

Projeto em Django 5.2 (LTS) — versão fixada em requirements.txt.

For more information on this file, see
https://docs.djangoproject.com/en/5.2/topics/settings/

For the full list of settings and their values, see
https://docs.djangoproject.com/en/5.2/ref/settings/
"""

import contextlib
import os
import sys
from pathlib import Path

import environ

# Configure Windows DLL search path for WeasyPrint (GTK3 / Pango / Cairo)
if sys.platform == 'win32':
    for _gtk_path in [
        r'C:\msys64\mingw64\bin',
        r'C:\msys64\ucrt64\bin',
        r'C:\Program Files\GTK3-Runtime-Win64\bin',
    ]:
        if os.path.isdir(_gtk_path):
            with contextlib.suppress(AttributeError, OSError):
                os.add_dll_directory(_gtk_path)
            if _gtk_path not in os.environ.get('PATH', ''):
                os.environ['PATH'] = f'{_gtk_path};{os.environ.get("PATH", "")}'

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

# Initialize environ
env = environ.Env(
    DEBUG=(bool, False),
    ALLOWED_HOSTS=(list, []),
    CSRF_TRUSTED_ORIGINS=(list, []),
)

# Read .env file if present
environ.Env.read_env(BASE_DIR / '.env')

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = env('SECRET_KEY')

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = env('DEBUG')

ALLOWED_HOSTS = env.list('ALLOWED_HOSTS')


# Application definition

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    # Third-party
    'simple_history',
    'taggit',
    'django_filters',
    # Local apps
    'academico.apps.AcademicoConfig',
    'banco_questoes',
    'provas.apps.ProvasConfig',
    'desempenho.apps.DesempenhoConfig',
]

# django-taggit
TAGGIT_CASE_INSENSITIVE = True

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    # Serve os estáticos coletados (STATIC_ROOT) direto do gunicorn em produção.
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'simple_history.middleware.HistoryRequestMiddleware',
]

ROOT_URLCONF = 'Examina.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                # Perfil (administrador / visão total) para menu e botões — ver academico.acesso.
                'banco_questoes.context_processors.perfil',
            ],
        },
    },
]

WSGI_APPLICATION = 'Examina.wsgi.application'


# Database
# https://docs.djangoproject.com/en/5.2/ref/settings/#databases

# -- Default (dev / production) -----------------------------------------------
DATABASES = {
    'default': env.db('DATABASE_URL', default=f'sqlite:///{BASE_DIR / "db.sqlite3"}'),
}

# MySQL-specific hardening: enforce utf8mb4, UTC session timezone, strict mode
_MYSQL_OPTIONS = {
    'charset': 'utf8mb4',
    'init_command': (
        "SET sql_mode='STRICT_TRANS_TABLES,NO_ZERO_IN_DATE,NO_ZERO_DATE,"
        "ERROR_FOR_DIVISION_BY_ZERO,NO_ENGINE_SUBSTITUTION';"
        "SET SESSION time_zone='+00:00';"
    ),
    'connect_timeout': 5,
}

if DATABASES['default'].get('ENGINE', '').endswith('mysql'):
    DATABASES['default'].setdefault('OPTIONS', {}).update(_MYSQL_OPTIONS)
    DATABASES['default'].setdefault('TEST', {})['CHARSET'] = 'utf8mb4'
    DATABASES['default'].setdefault('TEST', {})['COLLATION'] = 'utf8mb4_unicode_ci'

# -- Staging / Homologação (optional) -----------------------------------------
if env('STAGING_DATABASE_URL', default=''):
    DATABASES['staging'] = env.db('STAGING_DATABASE_URL')
    if DATABASES['staging'].get('ENGINE', '').endswith('mysql'):
        DATABASES['staging'].setdefault('OPTIONS', {}).update(_MYSQL_OPTIONS)

# -- Connection health ---------------------------------------------------------
DATABASES['default']['CONN_MAX_AGE'] = env.int('DB_CONN_MAX_AGE', default=0 if DEBUG else 600)
DATABASES['default']['CONN_HEALTH_CHECKS'] = not DEBUG


# Password validation
# https://docs.djangoproject.com/en/5.2/ref/settings/#auth-password-validators

AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]


# Internationalization
# https://docs.djangoproject.com/en/5.2/topics/i18n/

LANGUAGE_CODE = 'pt-br'

TIME_ZONE = 'America/Sao_Paulo'

USE_I18N = True

USE_TZ = True


# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/5.2/howto/static-files/

STATIC_URL = '/static/'
STATICFILES_DIRS = [
    BASE_DIR / 'banco_questoes' / 'static',
]
# Destino do `collectstatic` (servido pelo WhiteNoise em produção). Criado se ausente para
# o WhiteNoise não alertar em desenvolvimento/testes (onde os finders servem os arquivos).
STATIC_ROOT = Path(env('STATIC_ROOT', default=str(BASE_DIR / 'staticfiles')))
STATIC_ROOT.mkdir(parents=True, exist_ok=True)

# Media files (Uploads de Imagens e Documentos)
# Não há servidor público de mídia: os arquivos (folhas escaneadas, imagens de questões)
# são entregues pela view autenticada `Examina.views.media_protegida`.
MEDIA_URL = '/media/'
MEDIA_ROOT = Path(env('MEDIA_ROOT', default=str(BASE_DIR / 'media')))

STORAGES = {
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    # Compressão gzip/brotli sem manifesto: os templates já versionam os assets via ?v=…,
    # e o manifesto exigiria `collectstatic` antes de qualquer execução com DEBUG=False (testes).
    'staticfiles': {'BACKEND': 'whitenoise.storage.CompressedStaticFilesStorage'},
}

# Default primary key field type
# https://docs.djangoproject.com/en/5.2/ref/settings/#default-auto-field

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# ---------------------------------------------------------------------------
# Autenticação
# ---------------------------------------------------------------------------
LOGIN_URL = 'login'
LOGIN_REDIRECT_URL = 'banco_questoes:questao_list'
LOGOUT_REDIRECT_URL = 'login'

# Limite de tentativas de login (banco_questoes.seguranca_login): depois de
# LOGIN_MAX_TENTATIVAS senhas erradas do mesmo usuário em LOGIN_BLOQUEIO_MINUTOS, o login
# desse usuário fica bloqueado até a janela passar (também no /admin/).
AUTHENTICATION_BACKENDS = ['banco_questoes.auth_backends.ModelBackendComLimite']
LOGIN_MAX_TENTATIVAS = env.int('LOGIN_MAX_TENTATIVAS', default=5)
LOGIN_BLOQUEIO_MINUTOS = env.int('LOGIN_BLOQUEIO_MINUTOS', default=15)

# ---------------------------------------------------------------------------
# CSRF trusted origins (required when behind a reverse proxy)
# ---------------------------------------------------------------------------
CSRF_TRUSTED_ORIGINS = env.list('CSRF_TRUSTED_ORIGINS', default=[])

# ---------------------------------------------------------------------------
# Upload limits
# ---------------------------------------------------------------------------
# Maximum size (in MB) accepted for CSV/XLSX imports
CSV_UPLOAD_MAX_SIZE_MB = env.int('CSV_UPLOAD_MAX_SIZE_MB', default=5)

# ---------------------------------------------------------------------------
# Production-only security hardening
# https://docs.djangoproject.com/en/stable/topics/security/
# https://docs.djangoproject.com/en/stable/ref/middleware/#module-django.middleware.security
# ---------------------------------------------------------------------------
if not DEBUG:
    # -- Reverse proxy ------------------------------------------------------
    # Atrás de um proxy que termina o TLS (Nginx, Traefik, Caddy…), o Django recebe
    # HTTP puro; sem este cabeçalho, SECURE_SSL_REDIRECT entraria em loop de redirect.
    # Só habilite se o proxy SEMPRE define/sobrescreve X-Forwarded-Proto.
    if env.bool('BEHIND_PROXY', default=False):
        SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

    # -- HTTPS enforcement --------------------------------------------------
    # Redirect every HTTP request to HTTPS (desligue apenas se o proxy já redireciona).
    SECURE_SSL_REDIRECT = env.bool('SECURE_SSL_REDIRECT', default=True)

    # Instruct browsers to only use HTTPS for 1 year, including subdomains.
    # NOTE: Once deployed with HSTS, downgrading back to HTTP requires time.
    SECURE_HSTS_SECONDS = 31_536_000  # 1 year
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True

    # -- Secure cookies -----------------------------------------------------
    # Session and CSRF cookies are only sent over HTTPS.
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True

    # Prevent JavaScript from reading the session cookie (mitigates XSS theft).
    SESSION_COOKIE_HTTPONLY = True
    # CSRF_COOKIE_HTTPONLY intentionally left False (Django's JS helpers need it).

    # SameSite=Lax: cookies are not sent on cross-site sub-requests (images,
    # frames) but are sent when the user navigates to the site (e.g. clicking
    # a link). This is a good balance between security and usability.
    SESSION_COOKIE_SAMESITE = 'Lax'
    CSRF_COOKIE_SAMESITE = 'Lax'

    # -- Security headers ---------------------------------------------------
    # Prevent browsers from MIME-sniffing a response away from the declared
    # content-type (sets X-Content-Type-Options: nosniff).
    SECURE_CONTENT_TYPE_NOSNIFF = True

    # Sets Referrer-Policy header to avoid leaking full URLs to third parties.
    SECURE_REFERRER_POLICY = 'strict-origin-when-cross-origin'

    # X-Frame-Options is already set to DENY by XFrameOptionsMiddleware;
    # confirm the value here for explicitness.
    X_FRAME_OPTIONS = 'DENY'


# ---------------------------------------------------------------------------
# Logging
# https://docs.djangoproject.com/en/5.2/topics/logging/
# ---------------------------------------------------------------------------

LOG_LEVEL = env('LOG_LEVEL', default='DEBUG' if DEBUG else 'WARNING')
LOG_DIR = BASE_DIR / 'logs'
LOG_DIR.mkdir(exist_ok=True)

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    # -- Formatters ----------------------------------------------------------
    'formatters': {
        'verbose': {
            'format': '{asctime} {levelname} {name} {module}.{funcName}:{lineno} | {message}',
            'style': '{',
            'datefmt': '%Y-%m-%d %H:%M:%S',
        },
        'simple': {
            'format': '{levelname} {name} | {message}',
            'style': '{',
        },
    },
    # -- Filters -------------------------------------------------------------
    'filters': {
        'require_debug_false': {
            '()': 'django.utils.log.RequireDebugFalse',
        },
        'require_debug_true': {
            '()': 'django.utils.log.RequireDebugTrue',
        },
    },
    # -- Handlers ------------------------------------------------------------
    'handlers': {
        'console': {
            'level': 'DEBUG',
            'class': 'logging.StreamHandler',
            'formatter': 'simple',
        },
        'file': {
            'level': 'DEBUG',
            'class': 'logging.handlers.RotatingFileHandler',
            'filename': LOG_DIR / 'examina.log',
            'maxBytes': 5 * 1024 * 1024,  # 5 MB
            'backupCount': 5,
            'formatter': 'verbose',
            'encoding': 'utf-8',
        },
        'mail_admins': {
            'level': 'ERROR',
            'class': 'django.utils.log.AdminEmailHandler',
            'filters': ['require_debug_false'],
            'formatter': 'verbose',
        },
    },
    # -- Loggers -------------------------------------------------------------
    'loggers': {
        'django': {
            'handlers': ['console', 'file'],
            'level': 'INFO',
            'propagate': True,
        },
        'django.server': {
            'handlers': ['console', 'file'],
            'level': 'INFO',
            'propagate': False,
        },
        'django.utils.autoreload': {
            'handlers': ['file'],
            'level': 'INFO',
            'propagate': False,
        },
        'django.db.backends': {
            'handlers': ['file'],
            'level': 'INFO',
            'propagate': False,
        },
        'django.request': {
            'handlers': ['file', 'mail_admins'],
            'level': 'WARNING',
            'propagate': False,
        },
        'django.security': {
            'handlers': ['file', 'mail_admins'],
            'level': 'WARNING',
            'propagate': False,
        },
        'banco_questoes': {
            'handlers': ['console', 'file'],
            'level': LOG_LEVEL,
            'propagate': False,
        },
    },
}
