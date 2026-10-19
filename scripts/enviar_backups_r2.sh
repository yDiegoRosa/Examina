#!/usr/bin/env bash
# =============================================================================
# enviar_backups_r2.sh — Copia a pasta de backups (banco + mídia) para um bucket
# do Cloudflare R2, a cópia fora do servidor.
#
# Uso:
#   ./scripts/enviar_backups_r2.sh
#
# Só ENVIA: nada é apagado no R2 a partir do servidor (se o servidor for invadido,
# os backups de lá continuam a salvo). A retenção no R2 é feita pela regra de
# ciclo de vida do bucket — ver docs/backup-restore.md.
#
# Usa o rclone pela imagem Docker oficial (nada a instalar no servidor além do Docker).
#
# Variáveis de ambiente (obrigatórias):
#   R2_ACCOUNT_ID         ID da conta Cloudflare (aparece na página do R2)
#   R2_ACCESS_KEY_ID      Chave do token de API do R2 (Object Read & Write, só neste bucket)
#   R2_SECRET_ACCESS_KEY  Segredo do mesmo token
#   R2_BUCKET             Nome do bucket (ex.: examina-backups)
# Opcionais:
#   R2_PREFIXO            Pasta dentro do bucket (padrão: examina)
#   BACKUP_DIR            Pasta local dos backups (padrão: ./backups)
#   RCLONE_IMAGE          Imagem do rclone (padrão: rclone/rclone:1.74.2)
# =============================================================================

set -euo pipefail
export MSYS_NO_PATHCONV=1

BACKUP_DIR="${BACKUP_DIR:-$(dirname "$(realpath "$0")")/../backups}"
R2_PREFIXO="${R2_PREFIXO:-examina}"
RCLONE_IMAGE="${RCLONE_IMAGE:-rclone/rclone:1.74.2}"
LOG_FILE="${LOG_FILE:-$(dirname "$(realpath "$0")")/../logs/backup.log}"

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

mkdir -p "$(dirname "$LOG_FILE")"

for var in R2_ACCOUNT_ID R2_ACCESS_KEY_ID R2_SECRET_ACCESS_KEY R2_BUCKET; do
  [[ -n "${!var:-}" ]] || die "Variável ${var} não definida (ver docs/backup-restore.md)."
done
[[ -d "$BACKUP_DIR" ]] || die "Pasta de backups não encontrada: ${BACKUP_DIR}"

BACKUP_DIR="$(realpath "$BACKUP_DIR")"
DESTINO="r2:${R2_BUCKET}/${R2_PREFIXO}"

log "INFO" "=========================================="
log "INFO" "Enviando backups para o Cloudflare R2: ${DESTINO}"

# Configuração do rclone só por variáveis de ambiente: os segredos não aparecem na linha
# de comando (o docker repassa o valor com "-e NOME", sem "=valor").
export RCLONE_CONFIG_R2_TYPE=s3
export RCLONE_CONFIG_R2_PROVIDER=Cloudflare
export RCLONE_CONFIG_R2_REGION=auto
export RCLONE_CONFIG_R2_ENDPOINT="https://${R2_ACCOUNT_ID}.r2.cloudflarestorage.com"
export RCLONE_CONFIG_R2_ACCESS_KEY_ID="$R2_ACCESS_KEY_ID"
export RCLONE_CONFIG_R2_SECRET_ACCESS_KEY="$R2_SECRET_ACCESS_KEY"
# Token restrito ao bucket não pode listar/criar buckets.
export RCLONE_CONFIG_R2_NO_CHECK_BUCKET=true

if docker run --rm \
    -v "${BACKUP_DIR}:/backups:ro" \
    -e RCLONE_CONFIG_R2_TYPE -e RCLONE_CONFIG_R2_PROVIDER -e RCLONE_CONFIG_R2_REGION \
    -e RCLONE_CONFIG_R2_ENDPOINT -e RCLONE_CONFIG_R2_ACCESS_KEY_ID \
    -e RCLONE_CONFIG_R2_SECRET_ACCESS_KEY -e RCLONE_CONFIG_R2_NO_CHECK_BUCKET \
    "$RCLONE_IMAGE" copy /backups "$DESTINO" \
      --include "*.sql.gz" --include "media_*.tar.gz" \
      --stats-one-line --stats-log-level NOTICE; then
  log "INFO" "✅ Backups copiados para ${DESTINO}"
else
  die "Falha ao enviar os backups para o R2."
fi
log "INFO" "=========================================="
