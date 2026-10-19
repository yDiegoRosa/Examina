#!/usr/bin/env bash
# =============================================================================
# backup_mysql.sh — Backup automatizado do MySQL do projeto Examina
#
# Uso:
#   ./scripts/backup_mysql.sh [--retention-days N] [--backup-dir PATH]
#
# Variáveis de ambiente (ou .env):
#   MYSQL_DATABASE        Nome do banco (padrão: examina_db)
#   MYSQL_USER            Usuário MySQL (padrão: examina_user)
#   MYSQL_PASSWORD        Senha MySQL (obrigatório)
#   MYSQL_ROOT_PASSWORD   Senha root MySQL (usado como fallback)
#   MYSQL_CONTAINER       Nome do container Docker (padrão: examina_mysql)
#   BACKUP_DIR            Diretório de destino (padrão: ./backups)
#   RETENTION_DAYS        Dias de retenção dos backups (padrão: 7)
# =============================================================================

set -euo pipefail

# ---------------------------------------------------------------------------
# Configurações (valores padrão — sobrescritos por variáveis de ambiente)
# ---------------------------------------------------------------------------
MYSQL_CONTAINER="${MYSQL_CONTAINER:-examina_mysql}"
MYSQL_DATABASE="${MYSQL_DATABASE:-examina_db}"
MYSQL_USER="${MYSQL_USER:-examina_user}"
MYSQL_PASSWORD="${MYSQL_PASSWORD:-${MYSQL_ROOT_PASSWORD:-}}"
BACKUP_DIR="${BACKUP_DIR:-$(dirname "$(realpath "$0")")/../backups}"
RETENTION_DAYS="${RETENTION_DAYS:-7}"
LOG_FILE="${LOG_FILE:-$(dirname "$(realpath "$0")")/../logs/backup.log}"

# Sobrescrever pelo argumento --retention-days / --backup-dir se passados
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
BACKUP_FILE="${BACKUP_DIR}/${TIMESTAMP}.sql.gz"

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
log "INFO" "=========================================="
log "INFO" "Iniciando backup — container: ${MYSQL_CONTAINER}"
log "INFO" "Banco: ${MYSQL_DATABASE} | Destino: ${BACKUP_FILE}"

# Garantir diretórios
mkdir -p "$BACKUP_DIR" || die "Não foi possível criar ${BACKUP_DIR}"
mkdir -p "$(dirname "$LOG_FILE")" || die "Não foi possível criar diretório de logs"

# Verificar se container está rodando
if ! docker ps --format '{{.Names}}' | grep -q "^${MYSQL_CONTAINER}$"; then
  die "Container '${MYSQL_CONTAINER}' não está em execução. Inicie o Docker Compose primeiro."
fi

# Verificar credenciais
if [[ -z "$MYSQL_PASSWORD" ]]; then
  die "MYSQL_PASSWORD (ou MYSQL_ROOT_PASSWORD) não definida. Defina no .env ou como variável de ambiente."
fi

# ---------------------------------------------------------------------------
# Executar backup
# ---------------------------------------------------------------------------
log "INFO" "Executando mysqldump..."

if docker exec "$MYSQL_CONTAINER" \
    mysqldump \
      --user="$MYSQL_USER" \
      --password="$MYSQL_PASSWORD" \
      --single-transaction \
      --no-tablespaces \
      --routines \
      --triggers \
      --events \
      --add-drop-table \
      --create-options \
      --extended-insert \
      --set-gtid-purged=OFF \
      "$MYSQL_DATABASE" \
  | gzip > "$BACKUP_FILE"; then
  log "INFO" "Backup comprimido salvo em: ${BACKUP_FILE}"
else
  # Remover arquivo parcial em caso de falha
  rm -f "$BACKUP_FILE"
  die "mysqldump falhou. Backup abortado."
fi

# Verificar tamanho mínimo (arquivo > 1KB significa conteúdo real)
BACKUP_SIZE=$(stat -c%s "$BACKUP_FILE" 2>/dev/null || stat -f%z "$BACKUP_FILE")
if [[ "$BACKUP_SIZE" -lt 1024 ]]; then
  rm -f "$BACKUP_FILE"
  die "Backup gerado está vazio ou corrompido (${BACKUP_SIZE} bytes). Removido."
fi

log "INFO" "Tamanho do backup: $(du -sh "$BACKUP_FILE" | cut -f1)"

# ---------------------------------------------------------------------------
# Limpeza de backups antigos (retenção)
# ---------------------------------------------------------------------------
log "INFO" "Aplicando política de retenção: ${RETENTION_DAYS} dias..."
DELETED=$(find "$BACKUP_DIR" -name "*.sql.gz" -mtime "+${RETENTION_DAYS}" -print -delete | wc -l)
log "INFO" "Backups expirados removidos: ${DELETED}"

# Listar backups disponíveis
TOTAL=$(find "$BACKUP_DIR" -name "*.sql.gz" | wc -l)
log "INFO" "Total de backups retidos: ${TOTAL}"

log "INFO" "✅ Backup concluído com sucesso: ${BACKUP_FILE}"
log "INFO" "=========================================="
