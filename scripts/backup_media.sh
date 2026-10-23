#!/usr/bin/env bash
# =============================================================================
# backup_media.sh — Backup dos uploads do Examina (cartões escaneados e imagens
# das questões), que ficam no volume de mídia do container web.
#
# Uso:
#   ./scripts/backup_media.sh [--retention-days N] [--backup-dir PATH]
#
# Variáveis de ambiente:
#   WEB_CONTAINER   Nome do container Django (padrão: examina_django)
#   MEDIA_PATH      Pasta de mídia dentro do container (padrão: /app/media)
#   BACKUP_DIR      Diretório de destino (padrão: ./backups)
#   RETENTION_DAYS  Dias de retenção dos backups (padrão: 7)
#
# Gera backups/media_AAAA-MM-DD_HH-MM-SS.tar.gz. Rode junto com o backup_mysql.sh:
# banco e mídia precisam ser do mesmo momento para a restauração ficar coerente.
# =============================================================================

set -euo pipefail
# Git Bash (Windows): não converter /app/media num caminho do Windows ao chamar o docker.
export MSYS_NO_PATHCONV=1

# ---------------------------------------------------------------------------
# Configurações (valores padrão — sobrescritos por variáveis de ambiente)
# ---------------------------------------------------------------------------
WEB_CONTAINER="${WEB_CONTAINER:-examina_django}"
MEDIA_PATH="${MEDIA_PATH:-/app/media}"
BACKUP_DIR="${BACKUP_DIR:-$(dirname "$(realpath "$0")")/../backups}"
RETENTION_DAYS="${RETENTION_DAYS:-7}"
LOG_FILE="${LOG_FILE:-$(dirname "$(realpath "$0")")/../logs/backup.log}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --retention-days) RETENTION_DAYS="$2"; shift 2 ;;
    --backup-dir)     BACKUP_DIR="$2";     shift 2 ;;
    *) echo "Opção desconhecida: $1" >&2; exit 1 ;;
  esac
done

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
TIMESTAMP=$(date +%Y-%m-%d_%H-%M-%S)
BACKUP_FILE="${BACKUP_DIR}/media_${TIMESTAMP}.tar.gz"

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
# Pré-verificações
# ---------------------------------------------------------------------------
mkdir -p "$BACKUP_DIR" || die "Não foi possível criar ${BACKUP_DIR}"
mkdir -p "$(dirname "$LOG_FILE")" || die "Não foi possível criar diretório de logs"

log "INFO" "=========================================="
log "INFO" "Iniciando backup da mídia — container: ${WEB_CONTAINER}"
log "INFO" "Pasta: ${MEDIA_PATH} | Destino: ${BACKUP_FILE}"

if ! docker ps --format '{{.Names}}' | grep -q "^${WEB_CONTAINER}$"; then
  die "Container '${WEB_CONTAINER}' não está em execução. Inicie o Docker Compose primeiro."
fi

# ---------------------------------------------------------------------------
# Executar backup (tar dentro do container, gravado no host)
# ---------------------------------------------------------------------------
if docker exec "$WEB_CONTAINER" tar -czf - -C "$MEDIA_PATH" . > "$BACKUP_FILE"; then
  log "INFO" "Arquivo salvo em: ${BACKUP_FILE}"
else
  rm -f "$BACKUP_FILE"
  die "tar falhou. Backup da mídia abortado."
fi

# Integridade: o arquivo precisa abrir. (Mídia vazia é válida numa instalação nova.)
if ! gzip -t "$BACKUP_FILE" 2>/dev/null; then
  rm -f "$BACKUP_FILE"
  die "Backup da mídia corrompido. Removido."
fi
ARQUIVOS=$(tar -tzf "$BACKUP_FILE" | grep -vc '/$' || true)

log "INFO" "Arquivos no backup: ${ARQUIVOS} | Tamanho: $(du -sh "$BACKUP_FILE" | cut -f1)"

# ---------------------------------------------------------------------------
# Limpeza de backups antigos (retenção)
# ---------------------------------------------------------------------------
log "INFO" "Aplicando política de retenção: ${RETENTION_DAYS} dias..."
DELETED=$(find "$BACKUP_DIR" -name "media_*.tar.gz" -mtime "+${RETENTION_DAYS}" -print -delete | wc -l)
log "INFO" "Backups de mídia expirados removidos: ${DELETED}"

log "INFO" "✅ Backup da mídia concluído: ${BACKUP_FILE}"
log "INFO" "=========================================="
