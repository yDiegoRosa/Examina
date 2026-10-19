# Backup e Restauração — Runbook

Guia operacional para gerenciar os backups do projeto **Examina**: o banco de dados MySQL e a
mídia (cartões escaneados e imagens das questões, no volume de mídia do container web).
Os dois precisam ser copiados juntos — um banco sem as imagens (ou o contrário) não recupera o sistema.

---

## Visão geral

| Item | Valor |
|---|---|
| Engine | MySQL 8.0 |
| Container | `examina_mysql` |
| Banco principal | `examina_db` |
| Formato do backup | Banco: `.sql.gz` (mysqldump + gzip) • Mídia: `media_*.tar.gz` |
| Diretório de backups | `backups/` (raiz do projeto) |
| Container da mídia | `examina_django` (pasta `/app/media`) |
| Retenção local | 7 dias |
| Frequência automática | Diária às 02h00 (cron do servidor) |
| Teste de restauração (CI) | Semanal — domingo 00h Brasília |

---

## Pré-requisitos

- Docker em execução com o container `examina_mysql` ativo
- Variáveis de ambiente definidas no `.env` (em especial `MYSQL_PASSWORD`)
- Bash 4+ (Linux/macOS) — no Windows, use **Git Bash** ou **WSL**

---

## Scripts disponíveis

| Script | Finalidade |
|---|---|
| [`scripts/backup_mysql.sh`](../scripts/backup_mysql.sh) | Gera backup comprimido do banco |
| [`scripts/restore_mysql.sh`](../scripts/restore_mysql.sh) | Restaura backup do banco em produção |
| [`scripts/test_restore.sh`](../scripts/test_restore.sh) | Valida um backup do banco em banco isolado |
| [`scripts/backup_media.sh`](../scripts/backup_media.sh) | Gera backup da mídia (`media_*.tar.gz`) |
| [`scripts/restore_media.sh`](../scripts/restore_media.sh) | Restaura a mídia (sobrescreve arquivos de mesmo nome; não apaga os demais) |

Dar permissão de execução (primeira vez):

```bash
chmod +x scripts/*.sh
```

---

## Operações

### 1. Backup manual

```bash
# Backup com configurações padrão (lê variáveis do .env ou shell)
MYSQL_PASSWORD=sua_senha ./scripts/backup_mysql.sh

# Sobrescrever retenção ou diretório de destino
MYSQL_PASSWORD=sua_senha ./scripts/backup_mysql.sh \
  --retention-days 14 \
  --backup-dir /mnt/nas/examina-backups
```

O arquivo será salvo em `backups/YYYY-MM-DD_HH-MM-SS.sql.gz`.

Backup da mídia (aceita as mesmas opções):

```bash
./scripts/backup_media.sh
```

Gera `backups/media_YYYY-MM-DD_HH-MM-SS.tar.gz`. Mídia vazia (instalação nova) é válida.

---

### 2. Agendar backup automático (Linux/servidor)

Edite o crontab do servidor de produção:

```bash
crontab -e
```

Adicione a linha abaixo para execução **diária às 02h00** (banco e mídia, em sequência):

```cron
0 2 * * * cd /caminho/para/Examina && MYSQL_PASSWORD=sua_senha bash scripts/backup_mysql.sh >> logs/cron.log 2>&1 && bash scripts/backup_media.sh >> logs/cron.log 2>&1
```

> **Dica de segurança:** use um arquivo de credenciais protegido (`chmod 600`) em vez de expor a senha diretamente no crontab.

Alternativa com arquivo de credenciais:

```bash
# /etc/examina-backup.env  (chmod 600, dono: root ou usuário do serviço)
MYSQL_PASSWORD=sua_senha_aqui
MYSQL_DATABASE=examina_db
MYSQL_USER=examina_user
```

```cron
0 2 * * * cd /caminho/para/Examina && set -a; source /etc/examina-backup.env; set +a; bash scripts/backup_mysql.sh >> logs/cron.log 2>&1 && bash scripts/backup_media.sh >> logs/cron.log 2>&1
```

> **Cópia fora do servidor:** os arquivos em `backups/` ficam no mesmo disco do sistema — se o
> servidor for perdido, os backups vão junto. Depois do cron, copie a pasta para outro lugar
> (outra máquina, NAS ou armazenamento em nuvem). O destino ainda está por definir.

---

### 3. Teste de restauração (validação local)

Execute antes de qualquer janela de manutenção ou após qualquer mudança de schema:

```bash
# Testar o backup mais recente automaticamente
MYSQL_PASSWORD=sua_senha ./scripts/test_restore.sh

# Testar um backup específico
MYSQL_PASSWORD=sua_senha ./scripts/test_restore.sh backups/2026-08-27_02-00-00.sql.gz
```

Saída esperada em caso de sucesso:
```
[2026-08-27 02:05:00] [INFO] ✅ Teste de restauração PASSOU
```

---

### 4. Restauração em produção (passo a passo)

> ⚠️ **Execute este procedimento com o serviço web fora do ar para evitar inconsistências.**

```bash
# Passo 1: Parar o container Django
docker compose stop web

# Passo 2: Validar o backup antes de restaurar
MYSQL_PASSWORD=sua_senha ./scripts/test_restore.sh backups/2026-08-27_02-00-00.sql.gz

# Passo 3: (Opcional) Fazer backup do estado atual antes de sobrescrever
MYSQL_PASSWORD=sua_senha ./scripts/backup_mysql.sh

# Passo 4: Restaurar o backup escolhido
MYSQL_PASSWORD=sua_senha ./scripts/restore_mysql.sh backups/2026-08-27_02-00-00.sql.gz

# Passo 5: Verificar e subir o serviço novamente
docker compose up -d web
docker compose exec web python manage.py check

# Passo 6: Restaurar a mídia do mesmo momento (com o web no ar)
./scripts/restore_media.sh backups/media_2026-08-27_02-00-00.tar.gz
```

Esse procedimento (banco e mídia apagados e restaurados) foi ensaiado em 2026-10-19 numa cópia
local do ambiente de produção: dados e arquivos voltaram idênticos.

---

### 5. Verificar backups disponíveis

```bash
ls -lh backups/
```

---

## Teste automatizado no CI (GitHub Actions)

O workflow [`.github/workflows/backup-restore-test.yml`](../.github/workflows/backup-restore-test.yml) executa **toda semana** (domingo às 00h Brasília) e também pode ser disparado manualmente.

### O que ele testa?

1. Sobe MySQL 8.0 isolado via GitHub Actions `services:`
2. Aplica todas as migrations do Django
3. Insere dados de seed via `manage.py shell`
4. Executa `mysqldump` → arquivo `.sql.gz`
5. Cria banco isolado `examina_backup_test`
6. Restaura o backup nele
7. Valida:
   - Total de tabelas (mínimo: 5)
   - `banco_questoes_disciplina` → ≥ 1 linha
   - `banco_questoes_questao` → ≥ 1 linha
   - `auth_user` → ≥ 1 linha
8. Remove o banco de teste
9. Faz upload do arquivo `.sql.gz` como artefato do workflow (retido por 7 dias)

### Como disparar manualmente

Vá em **Actions → 🗄️ Backup — Restore Test → Run workflow**.

---

## Política de retenção

| Tipo | Retenção | Local |
|---|---|---|
| Backups locais | 7 dias | `backups/` |
| Artefatos do CI | 7 dias | GitHub Actions |

> Para retenção estendida, configure upload para S3/GCS adicionando um passo de `aws s3 cp` ou `gsutil cp` no workflow ou no `backup_mysql.sh`.

---

## Troubleshooting

### Container não encontrado

```
ERROR: Container 'examina_mysql' não está em execução.
```

**Solução:** `docker compose up -d db` e aguardar o health check passar.

---

### Aviso de privilégio `PROCESS` no mysqldump

```
mysqldump: Error: 'Access denied; you need (at least one of) the PROCESS privilege(s) ...' when trying to dump tablespaces
```

O `backup_mysql.sh` já usa `--no-tablespaces` (o usuário da aplicação não tem esse privilégio e
o Examina não usa tablespaces). Se o aviso aparecer, o script está desatualizado.

---

### Credenciais inválidas

```
ERROR 1045 (28000): Access denied for user 'examina_user'@'localhost'
```

**Solução:** Verifique `MYSQL_PASSWORD` no `.env`. O valor deve coincidir com `MYSQL_PASSWORD` definido no `docker-compose.yml`.

---

### Backup vazio ou corrompido

```
ERROR: Backup gerado está vazio ou corrompido (512 bytes). Removido.
```

**Causas comuns:**
- O banco não existe ou está vazio
- Permissões insuficientes do usuário MySQL
- MySQL ainda inicializando (aguarde o healthcheck)

**Diagnóstico:**
```bash
docker exec examina_mysql mysql -u examina_user -p -e "SHOW TABLES;" examina_db
```

---

### Teste de restauração falhou

```
ERROR: ❌ Tabela 'banco_questoes_questao': esperado >= 1 linha(s), encontrado: 0
```

**Ação:** O backup está íntegro estruturalmente, mas o banco estava vazio no momento do backup. Isso é esperado em ambientes recém-criados. Em produção, indica que dados não foram inseridos antes do backup.

---

## Checklist de DR (Disaster Recovery)

- [ ] Backup mais recente (banco **e** mídia) existe e tem menos de 25h
- [ ] Cópia dos backups fora do servidor está em dia
- [ ] `test_restore.sh` passou sem erros
- [ ] Documentação atualizada com credenciais de acesso ao servidor
- [ ] Cron agendado e ativo no servidor de produção (`crontab -l`)
- [ ] Alerta configurado (e-mail / Slack) em caso de falha do cron
- [ ] Workflow do CI executou com sucesso na última semana
