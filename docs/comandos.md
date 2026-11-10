# Comandos de manutenção

Comandos `manage.py` do projeto e quando usar cada um.

**Onde rodar:**
- Local: `python manage.py <comando>` com o `.venv` ativado.
- Servidor (Docker): `docker compose -f docker-compose.prod.yml exec web python manage.py <comando>`, dentro de `/opt/examina`.

## Comandos do Examina

| Comando | Para quê | Grava no banco? |
|---|---|---|
| `aplicar_retencao [--simular]` | Aplica os prazos de retenção LGPD: apaga fotos de cartões antigas, anonimiza alunos sem matrícula ativa há muito tempo e apaga tentativas de login e sessões vencidas | Sim (não com `--simular`) |
| `diagnosticar_omr <imagens…> [--questoes N] [--salvar-canonica PASTA]` | Mostra como o leitor óptico lê fotos reais (QR, marcadores, preenchimento de cada bolha) | Não |
| `avisar_administrador --assunto "…" [--mensagem "…"]` | Envia um e-mail aos `ADMINS` (sem `--mensagem`, lê o texto da entrada padrão). Usado pelo script de backup quando algo falha | Não |
| `criar_perfil_admin` | Cria o grupo `Administrador` com as permissões do acervo. Rodar uma vez na instalação | Sim |
| `seed_topicos [--limpar]` | Cadastra os tópicos curriculares das disciplinas ativas | Sim |
| `seed_enade [--limpar]` | Cadastra o acervo de teste do Simulado ENADE (origem "ENADE ADS (simulado)"). Idempotente; `--limpar` remove antes as questões do simulado que não estão em provas | Sim |

### `aplicar_retencao`

Roda sozinho todo dia, logo depois do backup (`scripts/backup_completo.sh`); falha gera aviso por
e-mail. Os prazos vêm do `.env`, e `0` desliga a regra:

| Variável | Padrão | Regra |
|---|---|---|
| `RETENCAO_FOTOS_DIAS` | 180 | foto do cartão (registro e arquivo), contada da data da aplicação; a nota fica |
| `RETENCAO_ALUNOS_DIAS` | 730 | aluno sem matrícula ativa, contado da última atividade → nome "Aluno anonimizado", matrícula `ANON-<id>` |
| `RETENCAO_TENTATIVAS_LOGIN_DIAS` | 30 | tentativas de login (com IP); sessões vencidas também são apagadas |

Depois de mudar um prazo, rode primeiro com `--simular` para ver os números. Os prazos aparecem
na página pública `/privacidade/`. Mais detalhes em [backup-restore.md](backup-restore.md).

### `diagnosticar_omr`

Use quando uma foto não é lida ou para calibrar os limites do leitor. Exemplos e interpretação
em [leitor-optico.md](leitor-optico.md#diagnóstico-e-calibração). No servidor, a imagem precisa
estar dentro do container: copie com `docker compose -f docker-compose.prod.yml cp foto.jpg web:/tmp/`
e passe `/tmp/foto.jpg`.

## Comandos do Django mais usados

| Comando | Para quê |
|---|---|
| `migrate` | Aplica as migrations (no servidor, o container já roda ao subir) |
| `createsuperuser` | Cria a conta de administrador |
| `changepassword <login>` | Troca a senha de uma conta pelo terminal (ex.: administrador que perdeu a senha) |
| `sendtestemail --admins` | Testa o envio de e-mail para os `ADMINS` |
| `showmigrations` | Confere quais migrations já foram aplicadas |
| `check --deploy` | Revisa as configurações de segurança de produção |

Professores redefinem a senha pela tela (Acadêmico → Professores → ícone da chave), não pelo
terminal. Login bloqueado por tentativas: no `/admin/`, "Tentativas de login malsucedidas" →
apagar as do usuário.

## Scripts (`scripts/`)

Rodam no servidor, em bash. Backup e restauração (`backup_completo.sh`, `backup_mysql.sh`,
`backup_media.sh`, `enviar_backups_r2.sh`, `restore_mysql.sh`, `restore_media.sh`,
`test_restore.sh`) estão em [backup-restore.md](backup-restore.md); instalação
(`preparar_servidor.sh`, `criar_env_producao.sh`) em [deploy.md](deploy.md).
