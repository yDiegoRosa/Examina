# Guia de deploy (produção)

Como colocar o Examina no ar num servidor com Docker, atrás de um proxy HTTPS da instituição.
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

O `.env` tem senhas: mantenha-o fora do Git (já está no `.gitignore`) e com acesso restrito (`chmod 600 .env`).

## 3. Subir o sistema

```bash
docker compose -f docker-compose.prod.yml up -d --build
docker compose -f docker-compose.prod.yml ps
```

O `web` só sobe depois que o MySQL responde. Ao iniciar, ele aplica as migrations pendentes.

## 4. Primeiro acesso

Crie o administrador:

```bash
docker compose -f docker-compose.prod.yml exec web python manage.py createsuperuser
```

Depois, em `https://<domínio>/admin/`:

1. **Usuários:** crie uma conta para cada professor e coloque-a no grupo **Docente** (ou **Coordenador**). Os grupos e as permissões são criados pelas migrations.
2. Cada professor pode trocar a própria senha no ícone de chave do menu.

Opcional:

```bash
# grupo "Administrador" com todas as permissões do banco de questões
docker compose -f docker-compose.prod.yml exec web python manage.py criar_perfil_admin
# tópicos curriculares das disciplinas
docker compose -f docker-compose.prod.yml exec web python manage.py seed_topicos
```

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

## 6. Conferir depois do deploy

- [ ] A tela de login abre com o visual completo (CSS carregado pelo WhiteNoise).
- [ ] Login com o administrador funciona e `/admin/` abre.
- [ ] O PDF de uma prova é gerado com acentos corretos (fontes do WeasyPrint na imagem).
- [ ] O ZIP de cartões de uma aplicação de teste baixa sem erro de timeout.
- [ ] O envio de uma foto de cartão funciona e a imagem aparece só para usuários logados.

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
| Ver os logs | `docker compose -f docker-compose.prod.yml logs -f web` (os logs do Django também ficam no volume `logs_data`, em `/app/logs/examina.log`). |
| Abrir um shell do Django | `docker compose -f docker-compose.prod.yml exec web python manage.py shell` |
| Desbloquear um usuário (limite de tentativas) | Admin → **Tentativas de login malsucedidas** → apagar as do usuário. |
| Redefinir a senha de um professor | Admin → **Usuários** → o usuário → formulário de senha. |
| Parar / iniciar | `docker compose -f docker-compose.prod.yml stop` / `start` |

## 9. Backup

O banco tem scripts de backup, restauração e teste no [runbook](backup-restore.md) (o container precisa se chamar `examina_mysql`, como no compose).

Atenção: os scripts cobrem só o MySQL. Os uploads (cartões escaneados e imagens das questões) ficam no volume `media_data` e ainda não têm backup automático. Esse planejamento ficou para depois, junto com a ida do banco para a nuvem.
