# Examina

> Plataforma de gestão de banco de questões, geração inteligente de provas e análise de desempenho pedagógico.

## Sobre o projeto

O Examina centraliza o ciclo completo de avaliação de uma instituição de ensino: cadastro e organização de um acervo de questões, montagem de provas alinhadas a critérios pedagógicos (dificuldade, conteúdo, tópico), correção — incluindo leitura óptica automatizada — e análise de desempenho dos alunos para apoiar decisões da coordenação e do corpo docente.

Projeto de uso interno institucional (instituição única, sem suporte a multi-tenant).

## Status atual

🚧 **Em desenvolvimento — Módulos 1 a 3 em andamento; base do Módulo 4 iniciada.**

| Módulo | Descrição | Status |
|---|---|---|
| 1 — Banco de Questões | Cadastro, importação em lote e organização das questões | 🚧 Em andamento |
| 2 — Geração de Provas | Montagem automática/manual de avaliações por máscara, versões A/B/C e exportação em PDF | 🚧 Em andamento |
| 3 — Correção e Reconhecimento (OMR) | Cadastros acadêmicos, folha-resposta nominal com QR, upload de folhas e leitura óptica (QR + deskew) | 🚧 Em andamento |
| 4 — Análise de Desempenho | Registro de resultados por questão (base) → dashboards e psicometria | 🚧 Base iniciada |

## Funcionalidades

**Módulo 1 — Banco de Questões**
- Cadastro manual de questões objetivas e discursivas, com taxonomia obrigatória (disciplina, tópico, dificuldade, tipo)
- Importação em lote via CSV/Excel, com validação e relatório de inconsistências (ver [formato de importação](docs/importacao-questoes.md))
- Busca, edição, inativação e versionamento do acervo

**Módulo 2 — Geração de Provas**
- Definição de "máscara" de prova (quantidade, distribuição por dificuldade e tópico)
- Seleção automática por máscara + seleção manual complementar
- Versões/cadernos A/B/C com embaralhamento de questões e alternativas
- Exportação em PDF com folha de rosto e gabarito do docente

**Cadastros Acadêmicos** *(app `academico` — base para correção e desempenho)*
- CRUD de Alunos e Turmas (turma escopada por período letivo)
- Tela de matrícula (matricular/desmatricular alunos numa turma, preservando o histórico)

**Módulo 3 — Correção e Reconhecimento (OMR)**
- Aplicação de uma prova a uma turma numa data (`ProvaAplicacao`)
- Atribuição round-robin das versões (A/B/C) por aluno matriculado, evitando vizinhos com a mesma prova
- Folha-resposta **nominal por aluno**, com marcadores fiduciais (ArUco) e QR único (token) — layout de coordenadas fixas para leitura óptica
- Exportação de **uma folha individual por aluno** (ZIP), além da folha de rosto/gabarito do docente
- Upload em lote de folhas escaneadas (foto de celular ou digitalização)
- Leitura óptica: **decodificação do QR** (identifica a folha) e **correção de perspectiva/deskew** via ArUco — *amostragem das bolhas e lançamento das notas em desenvolvimento*

**Módulo 4 — Análise de Desempenho** *(app `desempenho` — base iniciada)*
- Modelo de resultado por questão (`Desempenho`: acerto, resposta marcada, nota, correção automática/manual)
- Relatórios de fragilidades/pontos fortes da turma *(planejado)*
- Indicadores de psicometria básica — dificuldade e discriminação por questão *(planejado)*

## Arquitetura e stack tecnológica

| Camada | Tecnologia |
|---|---|
| Backend | Python + Django |
| Banco de dados | MySQL |
| Frontend | Django templates + design system CSS próprio (tema claro/escuro) + Bootstrap Icons; Chart.js para dashboards *(planejado)* |
| Importação/validação de dados | openpyxl + csv |
| Geração de PDF e QR Code | WeasyPrint + qrcode |
| Processamento de imagem (OMR) | OpenCV (QRCodeDetector + marcadores ArUco); pyzbar opcional |
| Processamento assíncrono | Celery + Redis *(planejado)* |
| Infraestrutura | Docker + GitHub Actions (CI) |
| Testes | pytest + Django test client |

## Estrutura do repositório

```
Examina/
├── Examina/             # configuração do projeto (settings, urls, wsgi/asgi)
├── banco_questoes/      # Módulo 1 — banco de questões
├── provas/              # Módulos 2 e 3 — provas (máscaras, versões, PDF), aplicação,
│                        #   folha-resposta/QR, upload e leitor OMR (services/omr.py)
├── academico/           # Cadastros acadêmicos — alunos, turmas e matrículas
├── desempenho/          # Módulo 4 — resultado da correção por questão (base)
├── docs/                # runbooks operacionais (ex.: backup-restore)
├── scripts/             # scripts de backup/restore MySQL
├── requirements.txt
└── manage.py
```

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

Projeto conduzido sob orientação da supervisão acadêmica/institucional.
