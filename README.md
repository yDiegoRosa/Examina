# Examina

> Plataforma de gestão de banco de questões, geração de provas, correção por cartão-resposta e análise de desempenho pedagógico — FAETERJ-Rio.

## Sobre o projeto

O Examina centraliza o ciclo completo de avaliação da instituição: cadastro e organização do acervo de questões, montagem de provas alinhadas a critérios pedagógicos (dificuldade, tópico, tipo), aplicação às turmas com cadernos e cartões-resposta nominais, correção por leitura óptica e análise do desempenho dos alunos.

Projeto de uso interno institucional (instituição única, sem multi-tenant). Usam o sistema os professores (grupo **Docente**), a coordenação (grupo **Coordenador**) e o administrador.

## Funcionalidades

**Módulo 1 — Banco de Questões**
- Cadastro de questões objetivas (A–E) e discursivas, com disciplina, tópico, dificuldade e histórico de alterações.
- Importação em lote por CSV/XLSX no modelo de 15 colunas, com relatório de inconsistências ([formato de importação](docs/importacao-questoes.md)).
- Busca, filtros, edição e inativação do acervo.

**Módulo 2 — Geração de Provas**
- Máscaras de prova: quantidade, distribuição por dificuldade e tópico, modalidade e número de cadernos (versões).
- Montagem automática por máscara ou manual (seleção livre no acervo, com pontos por questão).
- Simulado ENADE do Componente Específico (27 objetivas + 3 discursivas, 5 áreas, caderno único).
- Versões A/B/C… com embaralhamento de questões e alternativas; cadernos em blocos (discursivas primeiro, objetivas longas em largura total e curtas em duas colunas).
- PDFs do caderno (layout institucional ou ENADE), da capa e do gabarito do docente; pacote ZIP com todas as versões.

**Cadastros acadêmicos**
- Alunos, turmas (período, nome e turno) e matrículas.

**Módulo 3 — Aplicação e correção**
- Aplicação de uma prova a uma turma numa data, com distribuição das versões em **blocos alfabéticos equilibrados** ("de Ana a Diego, versão A").
- **Cadernos nominais** (nome, matrícula, turma, turno e data na capa) e **cartões-resposta nominais** com QR, em ZIP ou individualmente; quem entra na turma depois recebe material próprio sem alterar o dos demais.
- Ata de avaliação (lista de presença) no modelo do sistema acadêmico.
- Envio das fotos/digitalizações dos cartões, leitura óptica (QR + marcadores ArUco + bolhas) e correção automática das objetivas, com fila de revisão manual.
- Lançamento manual de notas das discursivas (e das objetivas sem cartão), com histórico de cada alteração.

**Módulo 4 — Desempenho**
- Notas finais da turma (proporcionais aos pontos de cada questão), com exportação XLSX/CSV.
- Dashboard de fragilidades por tópico e dificuldade (filtros por turma, aplicação e período).
- Psicometria básica: índice de facilidade por questão e itens fora do esperado (CSV/PDF).

**Segurança e conta**
- Acesso por perfil (Docente/Coordenador/administrador); mídia enviada só para usuários autenticados.
- Troca de senha pelo próprio usuário (ícone de chave no menu).
- Limite de tentativas de login: 5 senhas erradas em 15 minutos bloqueiam o usuário por 15 minutos (configurável).

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
| Produção | Docker, gunicorn + WhiteNoise, atrás de um proxy HTTPS |
| Qualidade | pytest + pytest-django, coverage (mínimo 80%), ruff, GitHub Actions |

## Estrutura do repositório

```
Examina/
├── Examina/          # configuração (settings, urls, wsgi) e a view de mídia protegida
├── banco_questoes/   # Módulo 1, login/senha, permissões e o design system (base.html, custom.css)
├── provas/           # Módulos 2 e 3: máscaras, provas, versões, PDFs, aplicações, cartões, OMR
├── academico/        # alunos, turmas e matrículas
├── desempenho/       # Módulo 4: resultados por questão, notas, dashboards e psicometria
├── docs/             # guias: deploy, backup/restore, importação de questões
├── scripts/          # backup/restore do MySQL
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

**Dados de apoio:** `python manage.py seed_topicos` (tópicos das disciplinas) e `python manage.py seed_enade` (acervo para o Simulado ENADE; `--limpar` remove antes as questões do simulado que não estão em provas).

## Testes e qualidade

```bash
python -m pytest                  # suíte completa (não use "manage.py test")
coverage run -m pytest && coverage report
ruff check . && ruff format --check .
```

Com o `.env` apontando para MySQL, o pytest cria o banco `test_<nome>`: não rode duas execuções ao mesmo tempo.

## Produção

Ver o [guia de deploy](docs/deploy.md) e o [runbook de backup e restauração](docs/backup-restore.md).

Projeto conduzido sob orientação da supervisão acadêmica/institucional.
