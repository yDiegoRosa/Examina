# Guia de deploy (produção)

Como colocar o Examina no ar num servidor com Docker: atrás de um proxy HTTPS da instituição
(seções 1 a 9) ou num servidor próprio com HTTPS automático (**Instalação numa VPS**, logo abaixo).
Para o dia a dia do banco de dados, ver também o [runbook de backup e restauração](backup-restore.md).

## Visão geral

```
navegador ──HTTPS──> proxy (Nginx/Caddy/Traefik) ──HTTP──> web: gunicorn :8000 ──> db: MySQL 8.0
                                                          (127.0.0.1:8000)         (sem porta no host)
```

O `docker-compose.prod.yml` sobe dois containers:

| Serviço | Container | O que faz |
|---|---|---|
| `db` | `examina_mysql` | MySQL 8.0. Acessível só pela rede interna do compose. Dados no volume `mysql_data`. |
| `web` | `examina_django` | Django + gunicorn. Aplica as migrations ao subir. Estáticos servidos pelo WhiteNoise (coletados no build da imagem). Uploads no volume `media_data` e logs no volume `logs_data`. |

O app escuta só em `127.0.0.1:8000` do servidor. O proxy HTTPS encaminha para ele.

## Instalação numa VPS (Ubuntu 24.04, HTTPS automático)

Para um servidor próprio (ex.: Hostinger KVM 2, 2 vCPU e 8 GB) **sem** proxy HTTPS da instituição.
O serviço `caddy` do compose obtém e renova sozinho o certificado gratuito (Let's Encrypt), e o
resto é igual ao das seções seguintes. Tempo estimado: cerca de 1 hora.

```
navegador ──HTTPS──> caddy :443 (certificado automático) ──> web: gunicorn :8000 ──> db: MySQL 8.0
```

### 0. Antes de começar

- VPS com **Ubuntu 24.04 LTS**, sem painel de controle, e o IP público dela.
- **DNS:** um registro **A** do subdomínio (ex.: `examina.seudominio.com.br`) apontando para o IP.
  Na Cloudflare, deixe em **"DNS only"** (nuvem cinza): pelo proxy deles, cada envio fica limitado
  a 100 MB e as requisições longas caem em 100 s. Confira com `nslookup examina.seudominio.com.br`
  (deve responder o IP do servidor) — o certificado só sai depois disso.
- Se o painel do provedor tiver firewall próprio, libere as portas **22, 80 e 443**.

### 1. Acessar o servidor e baixar o código

Do PowerShell do seu computador: `ssh root@IP_DO_SERVIDOR`. No servidor:

```bash
apt-get update && apt-get install -y git
# O repositório é privado: crie uma chave só de leitura para este servidor
ssh-keygen -t ed25519 -f ~/.ssh/examina_deploy -N "" -C "examina-servidor"
cat ~/.ssh/examina_deploy.pub
```

No GitHub: repositório → **Settings → Deploy keys → Add deploy key**, cole a chave pública
mostrada e deixe **desmarcado** "Allow write access". De volta ao servidor:

```bash
printf 'Host github.com\n  IdentityFile ~/.ssh/examina_deploy\n  IdentitiesOnly yes\n' >> ~/.ssh/config
git clone git@github.com:yDiegoRosa/Examina.git /opt/examina   # confirme o GitHub com "yes"
cd /opt/examina
```

### 2. Preparar o sistema e gerar a configuração

```bash
bash scripts/preparar_servidor.sh
bash scripts/criar_env_producao.sh examina.seudominio.com.br admin@seudominio.com.br
```

- `preparar_servidor.sh`: fuso de Brasília, atualizações automáticas de segurança, Docker,
  firewall (só SSH, 80 e 443) e 2 GB de swap.
- `criar_env_producao.sh`: gera o `.env` com senhas aleatórias (`DEBUG=False`, domínio,
  `COMPOSE_PROFILES=caddy`, `BEHIND_PROXY=True`) e o `/etc/examina-backup.env` do backup. As
  senhas não aparecem na tela e ficam só nesses arquivos (permissão 600).
- O e-mail dos avisos (SMTP) pode ser preenchido depois: `nano .env` (ver seção 2).

### 3. Subir o sistema

```bash
docker compose -f docker-compose.prod.yml up -d --build
docker compose -f docker-compose.prod.yml ps
docker compose -f docker-compose.prod.yml logs -f caddy   # "certificate obtained successfully"; Ctrl+C sai
```

O primeiro build leva alguns minutos. Depois abra `https://examina.seudominio.com.br` e siga a
seção 4 (**Primeiro acesso**).

### 4. Backup

Preencha os campos `R2_*` em `/etc/examina-backup.env` (`nano /etc/examina-backup.env`; os
valores estão no arquivo de credenciais do R2 que você guardou — nunca cole em chat), teste e
agende (detalhes no [runbook](backup-restore.md#2-agendar-a-rotina-automática-linuxservidor)):

```bash
bash scripts/backup_completo.sh
(crontab -l 2>/dev/null; echo '0 2 * * * cd /opt/examina && bash scripts/backup_completo.sh >> logs/cron.log 2>&1') | crontab -
```

Atualizar o sistema depois: `cd /opt/examina && git pull && docker compose -f docker-compose.prod.yml up -d --build`.

## 1. Requisitos do servidor

- Docker com o plugin Compose v2 (`docker compose version`).
- Um domínio e um proxy HTTPS na frente do app (o Examina nunca deve ficar exposto sem HTTPS).
- Git, para baixar e atualizar o código.

## 2. Baixar o código e configurar o `.env`

```bash
git clone <url-do-repositorio> examina
cd examina
cp .env.example .env
```

Edite o `.env`. Para produção, estes valores são obrigatórios:

| Variável | Valor |
|---|---|
| `SECRET_KEY` | Chave longa e aleatória. Gere com `python -c "import secrets; print(secrets.token_urlsafe(64))"`. |
| `DEBUG` | `False` |
| `ALLOWED_HOSTS` | Domínio do sistema, ex.: `examina.faeterj-rio.edu.br` |
| `CSRF_TRUSTED_ORIGINS` | Origem completa, ex.: `https://examina.faeterj-rio.edu.br` |
| `MYSQL_DATABASE`, `MYSQL_USER`, `MYSQL_PASSWORD`, `MYSQL_ROOT_PASSWORD` | Credenciais do MySQL do container (senhas fortes). |
| `DATABASE_URL` | `mysql://MYSQL_USER:MYSQL_PASSWORD@db:3306/MYSQL_DATABASE`, com os mesmos valores acima. O host é `db`, o nome do serviço no compose. |
| `BEHIND_PROXY` | `True` quando o proxy HTTPS envia o cabeçalho `X-Forwarded-Proto` (o normal). Sem isso, o redirecionamento para HTTPS entra em loop. |

Opcionais:

| Variável | Padrão | Para quê |
|---|---|---|
| `SECURE_SSL_REDIRECT` | `True` | Deixe `True`, a menos que o proxy já redirecione HTTP para HTTPS. |
| `GUNICORN_WORKERS` | `3` | Processos do gunicorn. |
| `GUNICORN_TIMEOUT` | `120` | Segundos por requisição. Os ZIPs de cartões e de cadernos e a correção em lote rodam dentro da requisição. |
| `WEB_BIND` / `WEB_PORT` | `127.0.0.1` / `8000` | Onde o app escuta no servidor. Use `WEB_BIND=0.0.0.0` só se o proxy estiver em outra máquina. |
| `LOGIN_MAX_TENTATIVAS` / `LOGIN_BLOQUEIO_MINUTOS` | `5` / `15` | Limite de tentativas de login por usuário. |
| `LOG_LEVEL` | `WARNING` | Nível de log com `DEBUG=False`. |
| `CSV_UPLOAD_MAX_SIZE_MB` | `5` | Tamanho máximo da planilha de importação de questões. |
| `IMPORT_ZIP_MAX_SIZE_MB` / `IMPORT_IMAGEM_MAX_SIZE_MB` | `50` / `5` | Importação com imagens: tamanho do `.zip` e de cada imagem (o proxy precisa aceitar o tamanho do ZIP). |

E-mail do administrador — recebe o aviso de falha no backup e os erros internos (500) do sistema:

| Variável | Exemplo | Para quê |
|---|---|---|
| `ADMINS` | `admin@instituicao.edu.br` | E-mail(s) do administrador, separados por vírgula. |
| `EMAIL_HOST` / `EMAIL_PORT` | `smtp.provedor.com` / `587` | Servidor SMTP. Sem `EMAIL_HOST`, nada é enviado (os e-mails só aparecem no log). |
| `EMAIL_HOST_USER` / `EMAIL_HOST_PASSWORD` | | Login do SMTP (em Gmail, uma senha de app). |
| `EMAIL_USE_SSL` | `False` | `True` para a porta 465; na 587 o padrão é STARTTLS. |
| `EMAIL_REMETENTE` | `examina@instituicao.edu.br` | Remetente dos e-mails (precisa ser aceito pelo SMTP). Vazio = a própria conta do SMTP. |

**Qual conta usar para enviar:** contas pessoais do Outlook.com/Hotmail **não servem** — a Microsoft
desligou o login por senha no SMTP (set/2024) e o Django só envia com usuário e senha. Elas podem
**receber** os avisos (em `ADMINS`). Para enviar, o mais simples é uma conta Gmail só do sistema:

1. Crie a conta e ligue a verificação em duas etapas (myaccount.google.com → Segurança).
2. Gere uma senha de app em myaccount.google.com/apppasswords (nome: "Examina") — 16 letras.
3. No `.env`: `EMAIL_HOST=smtp.gmail.com`, `EMAIL_PORT=587`, `EMAIL_USE_SSL=False`,
   `EMAIL_HOST_USER` = a conta, `EMAIL_HOST_PASSWORD` = a senha de app sem espaços.
4. Recrie o `web` para ler o `.env`: `docker compose -f docker-compose.prod.yml up -d`.

Para testar: `docker compose -f docker-compose.prod.yml exec web python manage.py sendtestemail --admins`
(confira também a caixa de spam na primeira vez).

O `.env` tem senhas: mantenha-o fora do Git (já está no `.gitignore`) e com acesso restrito (`chmod 600 .env`).

## 3. Subir o sistema

```bash
docker compose -f docker-compose.prod.yml up -d --build
docker compose -f docker-compose.prod.yml ps
```

O `web` só sobe depois que o MySQL responde. Ao iniciar, ele aplica as migrations pendentes.

## 4. Primeiro acesso

Crie o administrador, o grupo "Administrador" e os tópicos curriculares das disciplinas:

```bash
docker compose -f docker-compose.prod.yml exec web python manage.py createsuperuser
docker compose -f docker-compose.prod.yml exec web python manage.py criar_perfil_admin
docker compose -f docker-compose.prod.yml exec web python manage.py seed_topicos
```

Depois, entrando como administrador:

1. **Professores** (Acadêmico → Professores): informe o nome completo; o sistema cria o login
   `nome.sobrenome` (ex.: Ana Maria de Souza → `ana.souza`; repetido vira `ana.souza2`), coloca no
   grupo **Docente** e mostra **uma única vez** a senha temporária para repassar. No primeiro acesso o
   professor só consegue usar a tela de troca de senha até escolher a dele. Perdeu a senha? Ícone da
   chave ("Redefinir senha") na lista: gera outra temporária e volta a exigir a troca.
   Coordenador (vê tudo, altera só o que é seu) e Administrador (gerencia cadastros) continuam sendo
   criados em `/admin/`, nos grupos **Coordenador** e **Administrador**.
2. **Acadêmico:** turmas, alunos e matrículas (só o administrador cadastra). A turma é criada pela
   **disciplina**: o nome é a sigla dela (Segurança da Informação → `SEG`); se houver mais de uma turma
   da mesma disciplina no mesmo período e turno, use o complemento (`SEG-A`, `SEG-B`).
3. **Docências:** ao criar a turma, escolha o professor — a docência (professor × disciplina × turma) é
   criada junto. Outros professores: Acadêmico → Turmas → ícone "Docentes da turma".
   **Sem docência, o professor não vê turmas nem desempenho.**
4. **Questões:** a importação grava como autor quem importou, e o professor monta provas só com as
   questões dele — cada professor importa as suas (ou o administrador, se for ele a montar as provas).

## 5. Proxy HTTPS

O proxy precisa:

- encaminhar para `http://127.0.0.1:8000` com os cabeçalhos `Host` e `X-Forwarded-Proto`;
- ter timeout de leitura maior ou igual ao `GUNICORN_TIMEOUT` (120 s);
- aceitar requisições grandes: o envio de cartões escaneados aceita até 100 imagens de até 10 MB cada.

Exemplo com Nginx:

```nginx
server {
    listen 443 ssl;
    server_name examina.faeterj-rio.edu.br;
    # ssl_certificate / ssl_certificate_key da instituição

    client_max_body_size 200M;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_read_timeout 130s;
    }
}
```

Com Caddy, `reverse_proxy 127.0.0.1:8000` já envia `X-Forwarded-Proto`; ajuste só o limite de corpo e o timeout, se necessário.
Num servidor sem proxy, use o Caddy que já vem no compose (`deploy/Caddyfile`, seção **Instalação numa VPS**).

## 6. Conferir depois do deploy

- [ ] A tela de login abre com o visual completo (CSS carregado pelo WhiteNoise).
- [ ] Login com o administrador funciona e `/admin/` abre.
- [ ] O PDF de uma prova é gerado com acentos corretos (fontes do WeasyPrint na imagem).
- [ ] O ZIP de cartões de uma aplicação de teste baixa sem erro de timeout.
- [ ] O envio de uma foto de cartão funciona e a imagem aparece só para usuários logados.
- [ ] `sendtestemail --admins` chega ao e-mail do administrador.
- [ ] `bash scripts/backup_completo.sh` termina sem erro e os arquivos aparecem no bucket do R2.

## 7. Atualizar para uma nova versão

```bash
cd examina
git pull
docker compose -f docker-compose.prod.yml up -d --build
```

A imagem é reconstruída, os estáticos são coletados de novo e as migrations são aplicadas ao subir. Faça um backup do banco antes de atualizar ([runbook](backup-restore.md)).

## 8. Operação

| Tarefa | Como |
|---|---|
| Ver os logs | `docker compose -f docker-compose.prod.yml logs -f web` (os logs do Django também ficam no volume `logs_data`, em `/app/logs/examina.log`). Cada container guarda no máximo 5 arquivos de 10 MB de log (`x-logging` no compose); o mais antigo é descartado. |
| Retenção de dados (LGPD) | Todo dia, depois do backup: fotos de cartões apagadas 6 meses após a aplicação, alunos sem matrícula ativa há 2 anos anonimizados, tentativas de login de mais de 30 dias apagadas (prazos `RETENCAO_*` no `.env`; prévia com `manage.py aplicar_retencao --simular`). Página pública `/privacidade/`. |
| Tempo de sessão | No login, **Manter conectado neste computador** marcado = 2 semanas; desmarcado (padrão) = termina ao fechar o navegador ou após `SESSAO_INATIVIDADE_MINUTOS` sem uso (120 por padrão, no `.env`). |
| Abrir um shell do Django | `docker compose -f docker-compose.prod.yml exec web python manage.py shell` |
| Desbloquear um usuário (limite de tentativas) | Admin → **Tentativas de login malsucedidas** → apagar as do usuário. |
| Redefinir a senha de um professor | Admin → **Usuários** → o usuário → formulário de senha. |
| Parar / iniciar | `docker compose -f docker-compose.prod.yml stop` / `start` |

## 9. Backup

O banco tem scripts de backup, restauração e teste no [runbook](backup-restore.md) (o container precisa se chamar `examina_mysql`, como no compose).

A rotina `scripts/backup_completo.sh` faz o backup do MySQL e dos uploads (cartões escaneados e imagens das questões, no volume `media_data`), copia os dois para um bucket do Cloudflare R2 e avisa o administrador por e-mail se algo falhar. Configure o bucket, o arquivo `/etc/examina-backup.env` e o cron seguindo o [runbook](backup-restore.md#2-agendar-a-rotina-automática-linuxservidor).
