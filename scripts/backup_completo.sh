#!/usr/bin/env bash
# =============================================================================
# backup_completo.sh — Rotina diária de backup do Examina (é este que vai no cron):
#   1. backup do banco (backup_mysql.sh)
#   2. backup da mídia (backup_media.sh)
#   3. cópia dos dois para o Cloudflare R2 (enviar_backups_r2.sh)
#   4. retenção de dados (manage.py aplicar_retencao): fotos de cartões antigas, alunos
#      sem matrícula ativa há muito tempo, tentativas de login e sessões vencidas — só
#      depois de um backup bem-sucedido (o que for apagado fica nas cópias por até 35 dias)
# Se alguma etapa falhar, avisa o administrador por e-mail (ADMINS do .env do
# sistema, pelo container web) e termina com código 1.
#
# Uso (cron, diário às 02h00):
#   0 2 * * * cd /caminho/para/Examina && bash scripts/backup_completo.sh >> logs/cron.log 2>&1
#
# Variáveis de ambiente: lidas de BACKUP_ENV_FILE (padrão: /etc/examina-backup.env)
# quando o arquivo existe — MYSQL_*, R2_* e, se preciso, WEB_CONTAINER/MYSQL_CONTAINER.
# =============================================================================

set -uo pipefail
export MSYS_NO_PATHCONV=1

DIR="$(dirname "$(realpath "$0")")"
BACKUP_ENV_FILE="${BACKUP_ENV_FILE:-/etc/examina-backup.env}"
if [[ -f "$BACKUP_ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$BACKUP_ENV_FILE"
  set +a
fi
WEB_CONTAINER="${WEB_CONTAINER:-examina_django}"
LOG_FILE="${LOG_FILE:-${DIR}/../logs/backup.log}"
export LOG_FILE
mkdir -p "$(dirname "$LOG_FILE")"

FALHAS=()
bash "${DIR}/backup_mysql.sh" || FALHAS+=("backup do banco")
bash "${DIR}/backup_media.sh" || FALHAS+=("backup da mídia")
bash "${DIR}/enviar_backups_r2.sh" || FALHAS+=("cópia para o Cloudflare R2")

if [[ ${#FALHAS[@]} -eq 0 ]]; then
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] [INFO] Aplicando a retenção de dados (LGPD)..." | tee -a "$LOG_FILE"
  if docker exec "$WEB_CONTAINER" python manage.py aplicar_retencao 2>&1 | tee -a "$LOG_FILE"; then
    exit 0
  fi
  FALHAS+=("retenção de dados (aplicar_retencao)")
fi

# ---------------------------------------------------------------------------
# Aviso ao administrador
# ---------------------------------------------------------------------------
RESUMO=$(printf '%s, ' "${FALHAS[@]}")
RESUMO="${RESUMO%, }"
MENSAGEM="A rotina de backup do Examina falhou em $(hostname) às $(date '+%d/%m/%Y %H:%M').

Etapas com falha: ${RESUMO}.

Últimas linhas do log (${LOG_FILE}):
$(tail -n 40 "$LOG_FILE" 2>/dev/null)

O que fazer: ver a seção Troubleshooting de docs/backup-restore.md."

echo "[$(date '+%Y-%m-%d %H:%M:%S')] [ERROR] Falha: ${RESUMO}. Avisando o administrador..." | tee -a "$LOG_FILE"
if printf '%s\n' "$MENSAGEM" | docker exec -i "$WEB_CONTAINER" \
    python manage.py avisar_administrador --assunto "Falha no backup: ${RESUMO}"; then
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] [INFO] Aviso enviado por e-mail." | tee -a "$LOG_FILE"
else
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] [ERROR] Não foi possível enviar o aviso por e-mail (container ${WEB_CONTAINER} parado ou e-mail não configurado)." | tee -a "$LOG_FILE"
fi
exit 1
