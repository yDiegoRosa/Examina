#!/usr/bin/env bash
# =============================================================================
# test_restore.sh — Teste automatizado de restauração de backup MySQL
#
# Uso:
#   ./scripts/test_restore.sh [arquivo.sql.gz]
#
#   Se nenhum arquivo for passado, usa o backup mais recente em ./backups/
#
# O script:
#   1. Cria banco temporário isolado (examina_backup_test)
#   2. Restaura o backup nele
#   3. Valida tabelas e contagem de registros em tabelas-chave
#   4. Destroi o banco temporário
#   5. Retorna exit code 0 em sucesso, 1 em falha
#
# Variáveis de ambiente:
#   MYSQL_USER          Usuário MySQL (padrão: examina_user)
#   MYSQL_PASSWORD      Senha MySQL (obrigatório)
#   MYSQL_CONTAINER     Nome do container Docker (padrão: examina_mysql)
#   BACKUP_DIR          Diretório de backups (padrão: ./backups)
#   TEST_DB             Nome do banco temporário (padrão: examina_backup_test)
# =============================================================================

set -euo pipefail

# ---------------------------------------------------------------------------
# Configurações
# ---------------------------------------------------------------------------
MYSQL_CONTAINER="${MYSQL_CONTAINER:-examina_mysql}"
MYSQL_USER="${MYSQL_USER:-examina_user}"
MYSQL_PASSWORD="${MYSQL_PASSWORD:-${MYSQL_ROOT_PASSWORD:-}}"
BACKUP_DIR="${BACKUP_DIR:-$(dirname "$(realpath "$0")")/../backups}"
TEST_DB="${TEST_DB:-examina_backup_test}"
LOG_FILE="${LOG_FILE:-$(dirname "$(realpath "$0")")/../logs/backup.log}"

# Tabelas mínimas esperadas após restauração (ajuste conforme seu schema)
# Formato: "tabela:minimo_de_linhas"  — use 0 para só verificar existência
EXPECTED_TABLES=(
  "banco_questoes_disciplina:0"
  "banco_questoes_questao:0"
  "auth_user:0"
)

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
  cleanup
  exit 1
}

mysql_exec() {
  docker exec "$MYSQL_CONTAINER" \
    mysql \
      --user="$MYSQL_USER" \
      --password="$MYSQL_PASSWORD" \
      --silent \
      --skip-column-names \
      --execute="$1" \
    2>/dev/null
}

cleanup() {
  log "INFO" "Removendo banco de teste '${TEST_DB}'..."
  mysql_exec "DROP DATABASE IF EXISTS \`${TEST_DB}\`;" 2>/dev/null || true
  log "INFO" "Banco de teste removido."
}

# ---------------------------------------------------------------------------
# Validações iniciais
# ---------------------------------------------------------------------------
mkdir -p "$(dirname "$LOG_FILE")"

if [[ -z "$MYSQL_PASSWORD" ]]; then
  die "MYSQL_PASSWORD (ou MYSQL_ROOT_PASSWORD) não definida."
fi

if ! docker ps --format '{{.Names}}' | grep -q "^${MYSQL_CONTAINER}$"; then
  die "Container '${MYSQL_CONTAINER}' não está em execução."
fi

# ---------------------------------------------------------------------------
# Selecionar arquivo de backup
# ---------------------------------------------------------------------------
if [[ $# -ge 1 ]]; then
  BACKUP_FILE="$1"
else
  # Usar o backup mais recente disponível
  BACKUP_FILE=$(find "$BACKUP_DIR" -name "*.sql.gz" -type f | sort | tail -n 1)
  if [[ -z "$BACKUP_FILE" ]]; then
    die "Nenhum backup encontrado em '${BACKUP_DIR}'. Execute backup_mysql.sh primeiro."
  fi
fi

if [[ ! -f "$BACKUP_FILE" ]]; then
  die "Arquivo de backup não encontrado: ${BACKUP_FILE}"
fi

log "INFO" "=========================================="
log "INFO" "🧪 Iniciando teste de restauração"
log "INFO" "   Arquivo: ${BACKUP_FILE}"
log "INFO" "   Banco de teste: ${TEST_DB}"
log "INFO" "=========================================="

# ---------------------------------------------------------------------------
# Registrar trap para garantir limpeza mesmo em caso de erro
# ---------------------------------------------------------------------------
trap cleanup EXIT

# ---------------------------------------------------------------------------
# Passo 1: Criar banco de teste isolado
# ---------------------------------------------------------------------------
log "INFO" "[1/4] Criando banco de teste '${TEST_DB}'..."
mysql_exec "DROP DATABASE IF EXISTS \`${TEST_DB}\`;"
mysql_exec "CREATE DATABASE \`${TEST_DB}\` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"
log "INFO" "      Banco criado."

# ---------------------------------------------------------------------------
# Passo 2: Restaurar backup no banco de teste
# ---------------------------------------------------------------------------
log "INFO" "[2/4] Restaurando backup no banco de teste..."

if zcat "$BACKUP_FILE" | docker exec -i "$MYSQL_CONTAINER" \
    mysql \
      --user="$MYSQL_USER" \
      --password="$MYSQL_PASSWORD" \
      "$TEST_DB"; then
  log "INFO" "      Restauração concluída."
else
  die "Falha durante a restauração do backup."
fi

# ---------------------------------------------------------------------------
# Passo 3: Validar integridade
# ---------------------------------------------------------------------------
log "INFO" "[3/4] Validando integridade do banco restaurado..."

FAIL=0

# 3a. Contagem total de tabelas
TABLE_COUNT=$(mysql_exec "SELECT COUNT(*) FROM information_schema.TABLES WHERE TABLE_SCHEMA='${TEST_DB}';")
if [[ -z "$TABLE_COUNT" || "$TABLE_COUNT" -lt 1 ]]; then
  log "ERROR" "   ❌ Nenhuma tabela encontrada no banco restaurado."
  FAIL=1
else
  log "INFO" "   ✅ Total de tabelas restauradas: ${TABLE_COUNT}"
fi

# 3b. Verificar tabelas-chave e contagem mínima de registros
for entry in "${EXPECTED_TABLES[@]}"; do
  TABLE="${entry%%:*}"
  MIN_ROWS="${entry##*:}"

  # Verificar se a tabela existe
  EXISTS=$(mysql_exec "SELECT COUNT(*) FROM information_schema.TABLES WHERE TABLE_SCHEMA='${TEST_DB}' AND TABLE_NAME='${TABLE}';")
  if [[ "$EXISTS" -lt 1 ]]; then
    log "WARN" "   ⚠️  Tabela '${TABLE}' não encontrada (pode não existir neste backup)"
    continue
  fi

  ROW_COUNT=$(mysql_exec "SELECT COUNT(*) FROM \`${TEST_DB}\`.\`${TABLE}\`;")
  if [[ "$ROW_COUNT" -lt "$MIN_ROWS" ]]; then
    log "ERROR" "   ❌ Tabela '${TABLE}': esperado >= ${MIN_ROWS} linhas, encontrado ${ROW_COUNT}."
    FAIL=1
  else
    log "INFO" "   ✅ Tabela '${TABLE}': ${ROW_COUNT} linha(s) (mínimo: ${MIN_ROWS})"
  fi
done

# ---------------------------------------------------------------------------
# Passo 4: Cleanup (via trap EXIT)
# ---------------------------------------------------------------------------
log "INFO" "[4/4] Limpeza do banco de teste..."

# ---------------------------------------------------------------------------
# Resultado final
# ---------------------------------------------------------------------------
if [[ "$FAIL" -ne 0 ]]; then
  log "ERROR" "=========================================="
  log "ERROR" "❌ Teste de restauração FALHOU"
  log "ERROR" "=========================================="
  exit 1
fi

log "INFO" "=========================================="
log "INFO" "✅ Teste de restauração PASSOU"
log "INFO" "   Backup íntegro: ${BACKUP_FILE}"
log "INFO" "=========================================="
