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
| Frequência automática | Diária às 02h00 (cron do servidor, `scripts/backup_completo.sh`) |
| Cópia fora do servidor | Bucket do Cloudflare R2: 35 dias, protegidos contra exclusão nos primeiros 30 |
| Aviso de falha | E-mail para os administradores (`ADMINS` no `.env` do sistema) |
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
| [`scripts/enviar_backups_r2.sh`](../scripts/enviar_backups_r2.sh) | Copia `backups/` para o Cloudflare R2 (só envia; nunca apaga no R2) |
| [`scripts/backup_completo.sh`](../scripts/backup_completo.sh) | **Rotina do cron:** banco + mídia + R2; avisa o administrador por e-mail se algo falhar |

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

### 2. Agendar a rotina automática (Linux/servidor)

A rotina `scripts/backup_completo.sh` faz, em sequência, o backup do banco, o da mídia e a cópia
dos dois para o Cloudflare R2. Se alguma etapa falhar, manda um e-mail aos administradores
(`ADMINS` no `.env` do sistema, enviado pelo container web) e termina com código 1.

**a) Bucket no Cloudflare R2** (uma vez, no painel da Cloudflare):

1. **R2 → Create bucket**: nome `examina-backups` (o plano gratuito do R2 cobre 10 GB, bem mais
   que o necessário no começo).
2. No bucket, **Settings → Bucket lock rules**: regra com prefixo `examina/` e retenção de
   **30 dias**. Ninguém apaga nem substitui um backup nesse período, nem com o token — se o
   servidor for invadido, a cópia externa continua a salvo. (O script não é afetado: cada
   arquivo tem nome com data e hora e nada é sobrescrito nem apagado.)
3. No bucket, **Settings → Object lifecycle rules → Add rule**, prefixo `examina/`:
   - **excluir objetos depois de 35 dias** (é a retenção da cópia externa; a local continua
     7 dias). Precisa ser maior que o bloqueio, para a exclusão nunca coincidir com ele;
   - **cancelar carregamentos incompletos de várias partes depois de 7 dias** (limpa envios
     interrompidos no meio);
   - deixar **desmarcada** a transição para *Acesso infrequente* (não entra na cota gratuita,
     cobra por GB lido na restauração e tem permanência mínima de 30 dias).
4. **R2 → Manage API tokens → Create API token**: permissão **Object Read & Write**, aplicada
   **só ao bucket** `examina-backups`. Anote o *Access Key ID*, o *Secret Access Key* (aparece uma
   vez só) e o *Account ID* (fica na página inicial do R2). Nunca cole essas chaves em chat,
   e-mail ou repositório; elas vão só no arquivo do item b.

**b) Arquivo de credenciais no servidor** — `/etc/examina-backup.env`, `chmod 600`, dono root
ou o usuário do serviço (fica fora do projeto e do Git):

```bash
MYSQL_PASSWORD=sua_senha_aqui
MYSQL_DATABASE=examina_db
MYSQL_USER=examina_user
R2_ACCOUNT_ID=id_da_conta
R2_ACCESS_KEY_ID=chave_do_token
R2_SECRET_ACCESS_KEY=segredo_do_token
R2_BUCKET=examina-backups
```

**c) E-mail do aviso** — no `.env` do sistema (o mesmo do Django), `ADMINS` e o servidor SMTP
(ver [deploy](deploy.md#2-baixar-o-código-e-configurar-o-env)). Confira com:

```bash
docker compose -f docker-compose.prod.yml exec web python manage.py sendtestemail --admins
```

**d) Cron** — `crontab -e` e a linha abaixo (todo dia às 02h00):

```cron
0 2 * * * cd /caminho/para/Examina && bash scripts/backup_completo.sh >> logs/cron.log 2>&1
```

Rode uma vez à mão (`bash scripts/backup_completo.sh`) e confira os arquivos no bucket.
(Testado em 2026-10-19: envio ao bucket, segunda execução sem reenvio e exclusão recusada pelo
bloqueio com `ObjectLockedByBucketPolicy`.) O envio
para o R2 usa a imagem Docker oficial do rclone (baixada na primeira execução); não há nada a
instalar no servidor além do Docker.

> Limite: o aviso sai pelo container web. Se o Docker inteiro estiver parado, o e-mail também
> não sai — o registro fica em `logs/cron.log`. Vale olhar o bucket de vez em quando.

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

**Servidor perdido (restaurar a partir do R2):** suba o sistema num servidor novo
([deploy](deploy.md)), baixe do bucket o par banco + mídia do mesmo horário para a pasta
`backups/` e siga os passos acima. O jeito mais simples de baixar é pelo painel da Cloudflare
(R2 → `examina-backups` → `examina/` → arquivo → Download). Pela linha de comando:

```bash
set -a; source /etc/examina-backup.env; set +a
docker run --rm -v "$PWD/backups:/backups" \
  -e RCLONE_CONFIG_R2_TYPE=s3 -e RCLONE_CONFIG_R2_PROVIDER=Cloudflare -e RCLONE_CONFIG_R2_REGION=auto \
  -e RCLONE_CONFIG_R2_ENDPOINT="https://${R2_ACCOUNT_ID}.r2.cloudflarestorage.com" \
  -e RCLONE_CONFIG_R2_ACCESS_KEY_ID="$R2_ACCESS_KEY_ID" \
  -e RCLONE_CONFIG_R2_SECRET_ACCESS_KEY="$R2_SECRET_ACCESS_KEY" \
  -e RCLONE_CONFIG_R2_NO_CHECK_BUCKET=true \
  rclone/rclone:1.74.2 copy "r2:${R2_BUCKET}/examina/2026-08-27_02-00-00.sql.gz" /backups
```

(repita para o `media_….tar.gz` do mesmo horário.)

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
| Cópia externa | 35 dias (ciclo de vida); protegida contra exclusão nos primeiros 30 (bloqueio) | Cloudflare R2, `examina-backups/examina/` |
| Artefatos do CI | 7 dias | GitHub Actions |

### Retenção dos dados no sistema (LGPD)

Depois de um backup bem-sucedido, o `backup_completo.sh` roda `manage.py aplicar_retencao`, que aplica os
prazos aprovados pela coordenação (configuráveis no `.env`, `0` desliga a regra):

| Dado | Prazo (padrão) | O que acontece |
|---|---|---|
| Fotos dos cartões-resposta | `RETENCAO_FOTOS_DIAS=180` após a aplicação | Foto e arquivo apagados; a nota lançada continua |
| Alunos sem matrícula ativa | `RETENCAO_ALUNOS_DIAS=730` desde a última atividade | Nome vira "Aluno anonimizado" e a matrícula `ANON-<id>`; notas e estatísticas continuam, anônimas |
| Tentativas de login | `RETENCAO_TENTATIVAS_LOGIN_DIAS=30` | Apagadas; as sessões vencidas também |

Para ver o que seria afetado sem alterar nada: `docker compose -f docker-compose.prod.yml exec web python manage.py aplicar_retencao --simular`.
O que é apagado ainda fica nas cópias de segurança por até 35 dias. Os prazos aparecem para todos em `/privacidade/`.

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

### Falha no envio para o R2

```
ERROR: Variável R2_ACCOUNT_ID não definida
ERROR: Falha ao enviar os backups para o R2.
```

Confira o `/etc/examina-backup.env` (as quatro variáveis `R2_*`) e se o token continua válido e
com acesso ao bucket (painel → R2 → Manage API tokens). Para ver o erro completo, rode
`bash scripts/enviar_backups_r2.sh` à mão.

---

### O aviso por e-mail não chega

Rode `docker compose -f docker-compose.prod.yml exec web python manage.py sendtestemail --admins`.
Se der erro, revise `ADMINS`, `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`
e `EMAIL_REMETENTE` no `.env` do sistema (e recrie o container web para recarregar o `.env`). Sem
`EMAIL_HOST`, os e-mails só aparecem no log do container.

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
- [ ] Cópia dos backups no Cloudflare R2 está em dia (arquivos de hoje no bucket)
- [ ] `sendtestemail --admins` chega ao e-mail do administrador
- [ ] `test_restore.sh` passou sem erros
- [ ] Documentação atualizada com credenciais de acesso ao servidor
- [ ] Cron agendado e ativo no servidor de produção (`crontab -l`)
- [ ] Aviso por e-mail configurado (`ADMINS` + SMTP no `.env`)
- [ ] Workflow do CI executou com sucesso na última semana
