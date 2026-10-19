#!/usr/bin/env bash
# =============================================================================
# restore_media.sh — Restaura um backup da mídia (gerado pelo backup_media.sh)
# na pasta de mídia do container web.
#
# Uso:
#   ./scripts/restore_media.sh <media_AAAA-MM-DD_HH-MM-SS.tar.gz>
#
# Os arquivos do backup são gravados por cima dos atuais; arquivos que não estão
# no backup continuam onde estão (nada é apagado). Restaure junto com o backup do
# banco do mesmo momento.
#
# Variáveis de ambiente:
#   WEB_CONTAINER   Nome do container Django (padrão: examina_django)
#   MEDIA_PATH      Pasta de mídia dentro do container (padrão: /app/media)
# =============================================================================

set -euo pipefail
# Git Bash (Windows): não converter /app/media num caminho do Windows ao chamar o docker.
export MSYS_NO_PATHCONV=1

WEB_CONTAINER="${WEB_CONTAINER:-examina_django}"
MEDIA_PATH="${MEDIA_PATH:-/app/media}"
LOG_FILE="${LOG_FILE:-$(dirname "$(realpath "$0")")/../logs/backup.log}"
BACKUP_FILE="${1:-}"

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
  echo "Uso: $0 <media_AAAA-MM-DD_HH-MM-SS.tar.gz>" >&2
  exit 1
fi

mkdir -p "$(dirname "$LOG_FILE")"

[[ -f "$BACKUP_FILE" ]] || die "Arquivo não encontrado: ${BACKUP_FILE}"
[[ "$BACKUP_FILE" == *.tar.gz ]] || die "O arquivo deve ter extensão .tar.gz: ${BACKUP_FILE}"
gzip -t "$BACKUP_FILE" 2>/dev/null || die "Arquivo corrompido: ${BACKUP_FILE}"

if ! docker ps --format '{{.Names}}' | grep -q "^${WEB_CONTAINER}$"; then
  die "Container '${WEB_CONTAINER}' não está em execução."
fi

log "WARN" "=========================================="
log "WARN" "⚠️  RESTAURAÇÃO DA MÍDIA"
log "WARN" "   Arquivo:   ${BACKUP_FILE}"
log "WARN" "   Container: ${WEB_CONTAINER}:${MEDIA_PATH}"
log "WARN" "=========================================="

# Só pede confirmação se o terminal for interativo
if [[ -t 0 ]]; then
  read -r -p "❗ Os arquivos do backup vão SOBRESCREVER os de mesmo nome. Continuar? [s/N] " CONFIRM
  if [[ "${CONFIRM,,}" != "s" ]]; then
    echo "Restauração cancelada pelo usuário."
    exit 0
  fi
fi

# ---------------------------------------------------------------------------
# Executar restauração
# ---------------------------------------------------------------------------
if docker exec -i "$WEB_CONTAINER" tar -xzf - -C "$MEDIA_PATH" < "$BACKUP_FILE"; then
  TOTAL=$(tar -tzf "$BACKUP_FILE" | grep -vc '/$' || true)
  log "INFO" "✅ Mídia restaurada: ${TOTAL} arquivo(s) de ${BACKUP_FILE}"
else
  die "Falha ao extrair o backup no container."
fi
log "INFO" "=========================================="
