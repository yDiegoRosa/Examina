# Examina

> Plataforma de gestão de banco de questões, geração de provas, correção por cartão-resposta e análise de desempenho pedagógico — FAETERJ-Rio.

## Sobre o projeto

O Examina centraliza o ciclo completo de avaliação da instituição: cadastro e organização do acervo de questões, montagem de provas alinhadas a critérios pedagógicos (dificuldade, tópico, tipo), aplicação às turmas com cadernos e cartões-resposta nominais, correção por leitura óptica e análise do desempenho dos alunos.

Projeto de uso interno institucional (instituição única, sem multi-tenant). Usam o sistema os professores (grupo **Docente**), a coordenação (grupo **Coordenador**) e o administrador.

## Funcionalidades

**Módulo 1 — Banco de Questões**
- Cadastro de questões objetivas (A–E) e discursivas, com disciplina, tópico, dificuldade, imagem de apoio (PNG/JPG/GIF/WebP até 5 MB) e histórico de alterações.
- Disciplinas do catálogo oficial da faculdade ("Nome (SIGLA)"); a importação reconhece nome, sigla ou código ("4SEG") e não cria disciplinas.
- Importação em lote por CSV/XLSX no modelo de 15 colunas, ou por ZIP (planilha + imagens), com relatório linha a linha ([formato de importação](docs/importacao-questoes.md)).
- Autoria: o professor usa só as próprias questões; o administrador importa "em nome de" um professor e transfere a autoria em lote.
- Busca, filtros, edição e inativação do acervo.

**Módulo 2 — Geração de Provas**
- Máscaras de prova: quantidade, distribuição por dificuldade e tópico, modalidade e número de cadernos (versões).
- Montagem automática por máscara ou manual (seleção livre no acervo, com pontos por questão).
- Simulado ENADE do Componente Específico (27 objetivas + 3 discursivas, 5 áreas, caderno único).
- Versões A/B/C… com embaralhamento de questões e alternativas; cadernos em blocos (discursivas primeiro, objetivas longas em largura total e curtas em duas colunas).
- PDFs do caderno (layout institucional ou ENADE), da capa e do gabarito do docente; pacote ZIP com todas as versões.

**Cadastros acadêmicos** (só o administrador cadastra)
- Alunos e matrículas, um a um ou por planilha (`matricula` e `nome`); pela turma, a planilha já matricula todos.
- Turmas criadas pela disciplina (nome = sigla, com complemento quando há mais de uma: `SEG-A`), período e turno.
- Docências (professor × disciplina × turma), que definem o que cada professor vê.
- Aba Professores: cria a conta `nome.sobrenome` com senha temporária e troca obrigatória no primeiro acesso.

**Módulo 3 — Aplicação e correção**
- Aplicação de uma prova a uma turma numa data, com distribuição das versões em **blocos alfabéticos equilibrados** ("de Ana a Diego, versão A").
- **Cadernos nominais** (nome, matrícula, turma, turno e data na capa) e **cartões-resposta nominais** com QR, em ZIP ou individualmente; quem entra na turma depois recebe material próprio sem alterar o dos demais.
- Ata de avaliação (lista de presença) no modelo do sistema acadêmico.
- Envio das fotos/digitalizações dos cartões (o navegador reduz fotos grandes antes de enviar), leitura óptica (QR + marcadores ArUco + bolhas, com ajuste para folha curvada) e correção automática das objetivas, com fila de revisão manual ([leitor óptico](docs/leitor-optico.md)).
- Lançamento manual de notas das discursivas (0–10, contam proporcionalmente, sem nota de corte) e das objetivas sem cartão, com histórico de cada alteração.

**Módulo 4 — Desempenho**
- Notas finais da turma (proporcionais aos pontos de cada questão), com exportação XLSX/CSV.
- Dashboard de fragilidades por tópico e dificuldade (filtros por turma, aplicação e período).
- Psicometria básica: índice de facilidade por questão e itens fora do esperado (CSV/PDF).

**Segurança, conta e privacidade**
- Acesso por perfil: o professor vê só o que é dele; coordenação vê tudo; administrador gerencia os cadastros ([acesso por perfil](docs/acesso-por-perfil.md)). Mídia só para quem pode vê-la.
- Troca de senha pelo próprio usuário (ícone de chave no menu).
- Limite de tentativas de login: 5 senhas erradas em 15 minutos bloqueiam o usuário por 15 minutos (configurável).
- "Manter conectado": desmarcado, a sessão termina ao fechar o navegador ou após 2 h sem uso; páginas logadas não ficam no cache do navegador.
- Retenção de dados (LGPD): fotos de cartões apagadas após 6 meses, alunos sem matrícula ativa anonimizados após 2 anos, tentativas de login apagadas após 30 dias (prazos no `.env`), com página pública `/privacidade/`.

## Stack

| Camada | Tecnologia |
|---|---|
| Backend | Python 3.11 + Django 5.2 |
| Banco de dados | MySQL 8.0 (SQLite como alternativa local) |
| Frontend | Django templates + design system CSS próprio (tema claro/escuro) + Bootstrap Icons; Chart.js nos dashboards |
| PDF e QR Code | WeasyPrint + qrcode |
| Leitura óptica | OpenCV (QRCodeDetector + marcadores ArUco) |
| Importação | openpyxl + csv |
| Histórico | django-simple-history |
| Produção | Docker, gunicorn + WhiteNoise, Caddy (HTTPS automático) numa VPS; backup diário no Cloudflare R2 |
| Qualidade | pytest + pytest-django, coverage (mínimo 80%), ruff, GitHub Actions |

## Estrutura do repositório

```
Examina/
├── Examina/          # configuração, login, privacidade, mídia protegida, middlewares, retenção LGPD
├── banco_questoes/   # Módulo 1, importação, permissões, limite de login e o design system (base.html, custom.css)
├── provas/           # Módulos 2 e 3: máscaras, provas, versões, PDFs, aplicações, cartões, OMR
├── academico/        # alunos, turmas, matrículas, docências, professores e as regras de acesso por perfil
├── desempenho/       # Módulo 4: resultados por questão, notas, dashboards e psicometria
├── docs/             # documentação técnica e guias de operação (ver abaixo)
├── scripts/          # instalação do servidor, backup/restauração (MySQL + mídia + R2)
├── deploy/           # Caddyfile do proxy HTTPS
├── Dockerfile, docker-compose.yml (desenvolvimento), docker-compose.prod.yml (produção)
├── gunicorn.conf.py
└── requirements.txt
```

## Como rodar localmente

Pré-requisitos: Python 3.11, MySQL 8.0 (ou SQLite para testes rápidos) e, no Windows, o GTK3 runtime (dependências nativas do WeasyPrint para gerar PDFs).

```bash
python -m venv .venv
# Windows (PowerShell): .venv\Scripts\Activate.ps1
# Linux/macOS:          source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env        # ajuste SECRET_KEY e DATABASE_URL
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

No `.env`, o `DATABASE_URL` aponta para o banco que o `runserver` usa (ex.: `mysql://usuario:senha@127.0.0.1:3306/examina`). Sem `DATABASE_URL`, o Django usa SQLite.

**Com Docker (desenvolvimento):** `docker compose up --build` sobe o MySQL e o `runserver` com o código montado. Se já houver um MySQL local na porta 3306, defina `MYSQL_HOST_PORT=3307` no `.env`; dentro do compose o Django sempre usa `db:3306`.

**Dados de apoio:** `python manage.py seed_topicos` (tópicos das disciplinas) e `python manage.py seed_enade` (acervo para o Simulado ENADE; `--limpar` remove antes as questões do simulado que não estão em provas). Todos os comandos em [docs/comandos.md](docs/comandos.md).

## Testes e qualidade

```bash
python -m pytest                  # suíte completa (não use "manage.py test")
coverage run -m pytest && coverage report
ruff check . && ruff format --check .
```

Com o `.env` apontando para MySQL, o pytest cria o banco `test_<nome>`: não rode duas execuções ao mesmo tempo.

## Documentação

| Documento | Conteúdo |
|---|---|
| [Arquitetura e modelo de dados](docs/arquitetura.md) | apps, serviços, relações entre os dados e regras de negócio |
| [Acesso por perfil](docs/acesso-por-perfil.md) | o que professor, coordenador e administrador podem; como aplicar no código |
| [Cartão-resposta e leitor óptico](docs/leitor-optico.md) | geometria do cartão, como a leitura funciona, falhas e calibração |
| [Importação de questões](docs/importacao-questoes.md) | formato da planilha (15 colunas) e do ZIP com imagens |
| [Comandos de manutenção](docs/comandos.md) | comandos `manage.py` do projeto e quando usar |
| [Deploy](docs/deploy.md) | instalação no servidor, HTTPS, e-mail, atualização e primeiros passos |
| [Backup e restauração](docs/backup-restore.md) | rotina diária, R2, restauração e retenção |

## Produção

No ar numa VPS com Docker e Caddy. Ver o [guia de deploy](docs/deploy.md) e o [runbook de backup e restauração](docs/backup-restore.md).

Projeto conduzido sob orientação da supervisão acadêmica/institucional.
