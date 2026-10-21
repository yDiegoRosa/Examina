#!/usr/bin/env bash
# =============================================================================
# criar_env_producao.sh — Gera o .env de produção com senhas aleatórias, para um servidor
# próprio (VPS) com HTTPS automático pelo Caddy. Nunca sobrescreve um .env existente.
#
#   bash scripts/criar_env_producao.sh <domínio> [e-mail do administrador]
#   ex.: bash scripts/criar_env_producao.sh examina.exemplo.com.br admin@exemplo.com.br
#
# Rodando como root, cria também /etc/examina-backup.env (credenciais da rotina de backup),
# com as mesmas senhas do MySQL e os campos do Cloudflare R2 em branco para preencher.
# As senhas não são exibidas na tela.
# =============================================================================

set -euo pipefail

DOMINIO="${1:-}"
ADMIN_EMAIL="${2:-}"
if [[ -z "$DOMINIO" || "$DOMINIO" == *"://"* || "$DOMINIO" == */* ]]; then
  echo "Uso: $0 <domínio, sem https://> [e-mail do administrador]" >&2
  exit 1
fi

RAIZ="$(dirname "$(realpath "$0")")/.."
cd "$RAIZ"
if [[ -e .env ]]; then
  echo "Já existe um .env em $(realpath .) — não vou sobrescrever." >&2
  exit 1
fi

aleatorio() { openssl rand -base64 96 | tr -dc 'A-Za-z0-9' | head -c "$1"; }
SECRET_KEY="$(aleatorio 64)"
SENHA_DB="$(aleatorio 32)"
SENHA_ROOT="$(aleatorio 32)"

umask 077
cat > .env <<EOF
# Gerado por scripts/criar_env_producao.sh em $(date '+%Y-%m-%d %H:%M'). Guarde com cuidado (senhas).
SECRET_KEY=${SECRET_KEY}
DEBUG=False
ALLOWED_HOSTS=${DOMINIO}
CSRF_TRUSTED_ORIGINS=https://${DOMINIO}

# HTTPS automático (Caddy) neste servidor
DOMINIO=${DOMINIO}
COMPOSE_PROFILES=caddy
BEHIND_PROXY=True
SECURE_SSL_REDIRECT=True

# MySQL do container (rede interna do compose)
MYSQL_DATABASE=examina_db
MYSQL_USER=examina_user
MYSQL_PASSWORD=${SENHA_DB}
MYSQL_ROOT_PASSWORD=${SENHA_ROOT}
DATABASE_URL=mysql://examina_user:${SENHA_DB}@db:3306/examina_db

GUNICORN_WORKERS=3
GUNICORN_TIMEOUT=120
LOG_LEVEL=WARNING
LOGIN_MAX_TENTATIVAS=5
LOGIN_BLOQUEIO_MINUTOS=15

# E-mail do administrador (aviso de falha no backup e erros internos).
# Sem EMAIL_HOST nada é enviado. Teste: docker compose -f docker-compose.prod.yml exec web python manage.py sendtestemail --admins
ADMINS=${ADMIN_EMAIL}
EMAIL_HOST=
EMAIL_PORT=587
EMAIL_HOST_USER=
EMAIL_HOST_PASSWORD=
EMAIL_USE_SSL=False
EMAIL_REMETENTE=
EOF
chmod 600 .env
echo "✅ .env criado em $(realpath .env) (permissão 600)."

if [[ $EUID -eq 0 ]]; then
  if [[ -e /etc/examina-backup.env ]]; then
    echo "/etc/examina-backup.env já existe — mantido."
  else
    cat > /etc/examina-backup.env <<EOF
# Credenciais da rotina de backup (scripts/backup_completo.sh). Permissão 600.
MYSQL_DATABASE=examina_db
MYSQL_USER=examina_user
MYSQL_PASSWORD=${SENHA_DB}
# Cloudflare R2 — preencha (ver docs/backup-restore.md, seção 2):
R2_ACCOUNT_ID=
R2_ACCESS_KEY_ID=
R2_SECRET_ACCESS_KEY=
R2_BUCKET=examina-backups
EOF
    chmod 600 /etc/examina-backup.env
    echo "✅ /etc/examina-backup.env criado (preencha os campos R2_*)."
  fi
fi

echo
echo "Próximos passos:"
echo "  1. (opcional agora) preencha o e-mail SMTP no .env: nano .env"
echo "  2. docker compose -f docker-compose.prod.yml up -d --build"
