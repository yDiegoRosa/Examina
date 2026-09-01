# Examina

> Plataforma de gestão de banco de questões, geração inteligente de provas e análise de desempenho pedagógico.

## Sobre o projeto

O Examina centraliza o ciclo completo de avaliação de uma instituição de ensino: cadastro e organização de um acervo de questões, montagem de provas alinhadas a critérios pedagógicos (dificuldade, conteúdo, tópico), correção — incluindo leitura óptica automatizada — e análise de desempenho dos alunos para apoiar decisões da coordenação e do corpo docente.

Projeto de uso interno institucional (instituição única, sem suporte a multi-tenant).

## Status atual

🚧 **Em desenvolvimento — Módulos 1 (Banco de Questões) e 2 (Geração de Provas) em andamento.**

| Módulo | Descrição | Status |
|---|---|---|
| 1 — Banco de Questões | Cadastro, importação em lote e organização das questões | 🚧 Em andamento |
| 2 — Geração de Provas | Montagem automática/manual de avaliações por máscara, versões A/B/C e exportação em PDF | 🚧 Em andamento |
| 3 — Correção e Reconhecimento *(desejável)* | QR Code + leitura óptica de gabaritos | 📋 Planejado |
| 4 — Análise de Desempenho | Dashboards e psicometria básica | 📋 Planejado |

## Funcionalidades

**Módulo 1 — Banco de Questões**
- Cadastro manual de questões objetivas e discursivas, com taxonomia obrigatória (disciplina, tópico, dificuldade, tipo)
- Importação em lote via CSV, com validação e relatório de inconsistências
- Busca, edição, inativação e versionamento do acervo

**Módulo 2 — Geração de Provas**
- Definição de "máscara" de prova (quantidade, distribuição por dificuldade e tópico)
- Seleção automática por máscara + seleção manual complementar
- Exportação em PDF com folha de rosto e gabarito

**Módulo 3 — Correção e Reconhecimento** *(desejável)*
- Geração de QR Code por folha de prova
- Leitura óptica automatizada de questões objetivas

**Módulo 4 — Análise de Desempenho**
- Registro de acertos e erros por aluno e por questão
- Relatórios de fragilidades/pontos fortes da turma
- Indicadores de psicometria básica (dificuldade e discriminação por questão)

## Arquitetura e stack tecnológica

| Camada | Tecnologia |
|---|---|
| Backend | Python + Django |
| Banco de dados | PostgreSQL |
| Frontend | Django templates + Bootstrap 5 (Chart.js para dashboards) |
| Importação/validação de dados | pandas |
| Geração de PDF e QR Code | WeasyPrint + qrcode |
| Processamento de imagem (OMR) | OpenCV + pyzbar + marcadores ArUco |
| Processamento assíncrono | Celery + Redis |
| Infraestrutura | Docker + GitHub Actions (CI) |
| Testes | pytest + Django test client |

## Estrutura do repositório

```
Examina/
├── Examina/             # configuração do projeto (settings, urls, wsgi/asgi)
├── banco_questoes/      # Módulo 1 — banco de questões
├── provas/              # Módulo 2 — geração de provas (máscaras, versões A/B/C, PDF)
├── docs/                # runbooks operacionais (ex.: backup-restore)
├── scripts/             # scripts de backup/restore MySQL
├── requirements.txt
└── manage.py
```

> Os módulos 3 (Correção/OMR) e 4 (Análise de Desempenho) ainda não possuem apps neste repositório.

## Como rodar localmente

```bash
# clonar o repositório
git clone <url-do-repositorio>
cd examina

# criar e ativar ambiente virtual (pasta .venv)
python -m venv .venv
# Windows (PowerShell): .venv\Scripts\Activate.ps1
# Windows (cmd):        .venv\Scripts\activate
# Linux/macOS:          source .venv/bin/activate

# instalar dependências
pip install -r requirements.txt

# configurar variáveis de ambiente (banco de dados, secret key etc.)
cp .env.example .env

# aplicar migrações
python manage.py migrate

# subir o servidor de desenvolvimento
python manage.py runserver
```

> Instruções sujeitas a ajuste conforme o projeto avança — este README será atualizado a cada fase.

## Roadmap

| Fase | Escopo |
|---|---|
| Fase 0 — Fundação | Autenticação, perfis de acesso, modelagem de dados |
| Fase 1 — MVP | Módulo 1 completo + Módulo 2 sem versões múltiplas |
| Fase 2 — Diagnóstico básico | Módulo 4 com lançamento manual de notas + dashboard simples |
| Fase 3 — Correção automática | Módulo 3 (QR Code + leitura óptica) |
| Fase 4 — Refinamento | Psicometria avançada, versões múltiplas de prova |


## Equipe

- Diego
- *(demais integrantes)*

Projeto conduzido sob orientação da supervisão acadêmica/institucional.
