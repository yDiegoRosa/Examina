#!/usr/bin/env bash
# =============================================================================
# restore_mysql.sh — Restauração de backup MySQL do projeto Examina
#
# Uso:
#   ./scripts/restore_mysql.sh <arquivo.sql.gz> [--database NOME]
#
# ⚠️  ATENÇÃO: Este script SOBRESCREVE o banco de dados de destino!
#     Use com cuidado em produção. Prefira restore_mysql.sh para
#     restaurações em produção após confirmar o backup com test_restore.sh.
#
# Variáveis de ambiente (ou .env):
#   MYSQL_DATABASE      Nome do banco a restaurar (padrão: examina_db)
#   MYSQL_USER          Usuário MySQL (padrão: examina_user)
#   MYSQL_PASSWORD      Senha MySQL (obrigatório)
#   MYSQL_CONTAINER     Nome do container Docker (padrão: examina_mysql)
# =============================================================================

set -euo pipefail

# ---------------------------------------------------------------------------
# Configurações
# ---------------------------------------------------------------------------
MYSQL_CONTAINER="${MYSQL_CONTAINER:-examina_mysql}"
MYSQL_DATABASE="${MYSQL_DATABASE:-examina_db}"
MYSQL_USER="${MYSQL_USER:-examina_user}"
MYSQL_PASSWORD="${MYSQL_PASSWORD:-${MYSQL_ROOT_PASSWORD:-}}"
LOG_FILE="${LOG_FILE:-$(dirname "$(realpath "$0")")/../logs/backup.log}"

# ---------------------------------------------------------------------------
# Parse de argumentos
# ---------------------------------------------------------------------------
BACKUP_FILE=""
TARGET_DB=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --database) TARGET_DB="$2"; shift 2 ;;
    -*)         echo "Opção desconhecida: $1" >&2; exit 1 ;;
    *)          BACKUP_FILE="$1"; shift ;;
  esac
done

TARGET_DB="${TARGET_DB:-$MYSQL_DATABASE}"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
log() {
  local level="$1"; shift
  local msg="[$(date '+%Y-%m-%d %H:%M:%S')] [$level] $*"
  echo "$msg"
  echo "$msg" >> "$LOG_FILE"
}

die() {
  log "ERROR" "$*"
  exit 1
}

# ---------------------------------------------------------------------------
# Validações
# ---------------------------------------------------------------------------
if [[ -z "$BACKUP_FILE" ]]; then
  echo "Uso: $0 <arquivo.sql.gz> [--database NOME]" >&2
  exit 1
fi

if [[ ! -f "$BACKUP_FILE" ]]; then
  die "Arquivo não encontrado: ${BACKUP_FILE}"
fi

if [[ "$BACKUP_FILE" != *.sql.gz ]]; then
  die "O arquivo deve ter extensão .sql.gz: ${BACKUP_FILE}"
fi

if [[ -z "$MYSQL_PASSWORD" ]]; then
  die "MYSQL_PASSWORD (ou MYSQL_ROOT_PASSWORD) não definida."
fi

mkdir -p "$(dirname "$LOG_FILE")"

# ---------------------------------------------------------------------------
# Confirmação de segurança (produção)
# ---------------------------------------------------------------------------
log "WARN" "=========================================="
log "WARN" "⚠️  RESTAURAÇÃO DE BACKUP"
log "WARN" "   Arquivo:  ${BACKUP_FILE}"
log "WARN" "   Banco:    ${TARGET_DB}"
log "WARN" "   Container: ${MYSQL_CONTAINER}"
log "WARN" "=========================================="

# Só pede confirmação se o terminal for interativo
if [[ -t 0 ]]; then
  echo ""
  read -r -p "❗ Esta ação APAGARÁ todos os dados do banco '${TARGET_DB}'. Continuar? [s/N] " CONFIRM
  if [[ "${CONFIRM,,}" != "s" ]]; then
    echo "Restauração cancelada pelo usuário."
    exit 0
  fi
fi

# ---------------------------------------------------------------------------
# Verificar container em execução
# ---------------------------------------------------------------------------
if ! docker ps --format '{{.Names}}' | grep -q "^${MYSQL_CONTAINER}$"; then
  die "Container '${MYSQL_CONTAINER}' não está em execução."
fi

# ---------------------------------------------------------------------------
# Executar restauração
# ---------------------------------------------------------------------------
log "INFO" "Iniciando restauração de '${BACKUP_FILE}' → banco '${TARGET_DB}'..."

if zcat "$BACKUP_FILE" | docker exec -i "$MYSQL_CONTAINER" \
    mysql \
      --user="$MYSQL_USER" \
      --password="$MYSQL_PASSWORD" \
      "$TARGET_DB"; then
  log "INFO" "Restauração concluída no banco '${TARGET_DB}'."
else
  die "Falha durante a restauração. Verifique o log e o estado do banco."
fi

# ---------------------------------------------------------------------------
# Verificação pós-restauração: contagem de tabelas
# ---------------------------------------------------------------------------
log "INFO" "Verificando integridade pós-restauração..."

TABLE_COUNT=$(docker exec "$MYSQL_CONTAINER" \
  mysql \
    --user="$MYSQL_USER" \
    --password="$MYSQL_PASSWORD" \
    --silent \
    --skip-column-names \
    --execute="SELECT COUNT(*) FROM information_schema.TABLES WHERE TABLE_SCHEMA='${TARGET_DB}';" \
  2>/dev/null)

if [[ -z "$TABLE_COUNT" || "$TABLE_COUNT" -lt 1 ]]; then
  die "Verificação falhou: banco '${TARGET_DB}' não contém tabelas após a restauração."
fi

log "INFO" "✅ Restauração validada: ${TABLE_COUNT} tabelas encontradas no banco '${TARGET_DB}'."
log "INFO" "=========================================="
