# Arquitetura e modelo de dados

Visão técnica do Examina para quem vai manter o código: como os apps se dividem, como os dados
se ligam e quais regras de negócio não são óbvias olhando só para os models.

## Visão geral

Monólito Django 5.2 (Python 3.11), renderizado no servidor (templates + CSS próprio, sem SPA),
com MySQL 8 em produção. PDFs saem do WeasyPrint; a leitura óptica usa OpenCV. Instituição
única (sem multi-tenant).

```
Navegador ──HTTPS──▶ Caddy (TLS, proxy) ──▶ gunicorn + Django (WhiteNoise serve os estáticos)
                                                │
                                                ├── MySQL 8 (dados)
                                                └── volume media_data (imagens das questões e fotos dos cartões)
```

Detalhes de produção em [deploy.md](deploy.md); backup e restauração em [backup-restore.md](backup-restore.md).

## Apps

| App | Responsabilidade | Peças principais |
|---|---|---|
| `Examina/` | Configuração, login, troca de senha, página de privacidade, mídia protegida, middlewares, retenção LGPD | `settings.py`, `views.py`, `middleware.py`, `retencao.py` |
| `banco_questoes/` | Módulo 1: disciplinas, tópicos, questões, alternativas, importação; permissões; design system (`base.html`, `custom.css`); limite de tentativas de login | `models.py`, `importer.py`, `importacao_zip.py`, `validator.py`, `disciplinas.py`, `permissions.py`, `seguranca_login.py` |
| `academico/` | Alunos, turmas, matrículas, docências, contas de professor; **regras de acesso por perfil** | `models.py`, `acesso.py`, `professores.py`, `importacao_alunos.py`, `middleware.py` |
| `provas/` | Módulos 2 e 3: máscaras, provas, versões, cadernos, aplicações, cartões-resposta, ata, leitor óptico e correção | `models.py`, `services/` (ver abaixo), `presets.py` |
| `desempenho/` | Módulo 4: resultado por questão, notas finais, dashboard e psicometria | `models.py`, `services.py` |

A lógica de negócio fica em módulos de serviço (`provas/services/`, `desempenho/services.py`,
`academico/acesso.py` etc.), não nas views. As views cuidam de permissão, formulário e resposta.

### Serviços de `provas/services/`

| Módulo | O que faz |
|---|---|
| `question_selector.py`, `stock_validator.py`, `matrix_calculator.py` | Montagem por máscara: confere o estoque do acervo e sorteia as questões pela distribuição de dificuldade e tópico |
| `manual_generator.py`, `question_replacer.py` | Montagem manual e troca de questão de uma prova |
| `snapshot_builder.py` | "Congela" a máscara e cada questão no momento em que entram na prova |
| `version_generator.py` | Gera as versões A/B/C… (ordem das questões e das alternativas embaralhadas) |
| `caderno_layout.py`, `caderno_service.py`, `pdf_generator.py` | Caderno em blocos (discursivas primeiro, objetivas longas e curtas) e os PDFs |
| `version_assigner.py` | Distribui as versões aos alunos em blocos alfabéticos |
| `answer_sheet_layout.py`, `fiducial.py`, `folha_service.py` | Geometria, marcadores/QR e PDF do cartão-resposta nominal |
| `ata_service.py` | Ata de avaliação (lista de presença) |
| `omr.py`, `correcao.py` | Leitura óptica e correção automática — ver [leitor-optico.md](leitor-optico.md) |

## Modelo de dados

```
Disciplina ─┬─< Topico
            ├─< Questao ─< Alternativa                    (Questao tem histórico)
            ├─< Turma ─< Matricula >─ Aluno
            └─< Docencia >─ professor (User), Turma

Mascara ─< MascaraCriterioDificuldade / MascaraCriterioTopico   (Mascara tem histórico)
   │
   ▼
Prova ─< ProvaQuestao >─ Questao          (snapshot_questao congela a questão; Prova tem histórico)
  │
  ├─< ProvaVersao ─< ProvaVersaoItem >─ ProvaQuestao   (ordem e alternativas embaralhadas = gabarito da versão)
  │
  └─< ProvaAplicacao >─ Turma (prova × turma × data)
          ├─< AtribuicaoVersao >─ Aluno, ProvaVersao     (que versão cada aluno recebe)
          ├─< FolhaResposta >─ Aluno, ProvaVersao        (cartão nominal, qr_token único)
          │        └─< Desempenho >─ Questao             (resposta e nota por questão; tem histórico)
          └─< FolhaDigitalizada ─> FolhaResposta         (foto enviada, fila do leitor óptico)
```

Outros: `PerfilProfessor` (1:1 com o usuário; marca a troca de senha obrigatória) e
`TentativaLogin` (senhas erradas, para o limite de tentativas).

Unicidades importantes:

- `Aluno.matricula` é única; `Matricula` é única por (aluno, turma).
- `Turma` é única por (período, nome, turno). O nome é montado da disciplina: sigla + complemento (`SEG`, `SEG-A`).
- `Docencia` é única por (professor, disciplina, turma).
- `ProvaAplicacao` é única por (prova, turma, data); `FolhaResposta` e `AtribuicaoVersao` por (aplicação, aluno).
- `Desempenho` é único por (folha-resposta, questão).

Exclusões: quase todas as chaves estrangeiras de dados acadêmicos e de prova são `PROTECT`
(não se apaga aluno, turma, questão ou versão que já foi usada). Questão e turma são
**inativadas**, nunca apagadas pela tela.

## Regras de negócio que não aparecem nos models

**Disciplinas oficiais.** As disciplinas são o catálogo oficial da faculdade, no formato
"Nome (SIGLA)" (`banco_questoes/disciplinas.py`). A importação de questões reconhece nome, sigla
ou código com período ("4SEG") e **nunca cria** disciplina nova.

**Questão congelada na prova.** Ao entrar numa prova, a questão é copiada para
`ProvaQuestao.snapshot_questao` (e a máscara para `Prova.snapshot_mascara` e afins). Editar a
questão no acervo depois não muda provas já montadas.

**Prova travada depois de distribuída.** `Prova.versoes_distribuidas` fica verdadeiro assim que
existe uma `AtribuicaoVersao` ou uma `FolhaResposta`. A partir daí não se regera versão nem se
troca questão: isso invalidaria gabaritos, cartões impressos e notas.

**Gabarito por versão.** Cada `ProvaVersaoItem` guarda a ordem da questão e as alternativas
embaralhadas daquela versão. A letra correta de um aluno depende da versão do cartão dele.

**Versões em blocos alfabéticos.** Na aplicação, os alunos em ordem alfabética são divididos
em faixas contínuas, uma por versão, de tamanhos iguais (as primeiras recebem um aluno a mais
quando a conta não fecha). Quem entra na turma depois recebe versão sem mudar a dos outros.

**Cartão nominal.** Cada aluno tem uma `FolhaResposta` com `qr_token` (UUID). O QR do cartão
leva `EXM1F|<token>`, que identifica aluno, aplicação e versão. O cartão só tem as objetivas.

**Correção e nota.**
- Objetiva: o leitor óptico (ou o lançamento manual) grava `resposta_marcada` e `acertou`.
  Correção manual nunca é sobrescrita por uma nova leitura automática.
- Discursiva: o professor lança a nota (0–10) e ela conta proporcionalmente; não há nota de corte.
- Nota final = Σ(pontos da questão × fração obtida) ÷ Σ(pontos) × valor total da prova
  (`desempenho/services.py: notas_da_aplicacao`). Questão ainda não corrigida conta zero e marca
  a linha como incompleta.
- Dashboard e psicometria usam a mesma fração (`FRACAO_ACERTO`): objetiva 1/0, discursiva nota/10.
  Índice de facilidade abaixo de 0,30 = difícil; acima de 0,70 = fácil.

**Simulado ENADE.** Máscara com o preset do Componente Específico (27 objetivas + 3 discursivas,
caderno único). Só o administrador gera.

**Autoria.** O professor vê e usa só as questões de que é autor. O administrador pode importar
"em nome de" um professor e transferir a autoria em lote na lista de questões.

**Histórico.** `Questao`, `Mascara`, `Prova` e `Desempenho` têm histórico (django-simple-history),
com usuário e motivo; a tela de histórico da questão mostra as diferenças campo a campo.

## Pedido HTTP: middlewares

Além dos padrões do Django, em ordem (`Examina/settings.py`):

1. `SemCacheParaUsuarioLogadoMiddleware` — páginas de usuário logado saem com `no-store` (voltar depois de "Sair" não mostra nada).
2. `EncerraSessaoInativaMiddleware` — sem "Manter conectado", a sessão termina após `SESSAO_INATIVIDADE_MINUTOS` sem uso.
3. `TrocaDeSenhaObrigatoriaMiddleware` — professor com senha temporária só acessa "Alterar senha".
4. `HistoryRequestMiddleware` — registra o usuário nas versões do histórico.

O login (`Examina/views.py: LoginView`) usa o backend `ModelBackendComLimite`: depois de
`LOGIN_MAX_TENTATIVAS` senhas erradas em `LOGIN_BLOQUEIO_MINUTOS`, o usuário fica bloqueado
(vale também para o `/admin/`).

## Mídia

Não há servidor público de `/media/`: todo arquivo passa pela view `media_protegida`, que exige
login e aplica o acesso por perfil ([acesso-por-perfil.md](acesso-por-perfil.md)). Nos PDFs, o
WeasyPrint lê `/static` e `/media` direto do disco (`resolve_static_media_url`).

## Rotas

| Prefixo | App |
|---|---|
| `/` e `/questoes/…` | `banco_questoes` (acervo, importação, histórico) |
| `/provas/…` | `provas` (máscaras, provas, versões, aplicações, cartões, correção) |
| `/academico/…` | `academico` (alunos, turmas, matrículas, docências, professores) |
| `/desempenho/…` | `desempenho` (notas, dashboard, psicometria) |
| `/login/`, `/senha/alterar/`, `/privacidade/` | `Examina` |
| `/media/<caminho>` | mídia protegida |
| `/admin/` | admin do Django (contas de coordenador e administrador; desbloquear login apagando as tentativas) |

## Testes e qualidade

- `pytest` acha `tests.py` e `tests/test_*.py` (`pytest.ini`). Não use `manage.py test`.
- Cobertura mínima de 80% e `ruff==0.16.4` no CI (`.github/workflows/ci.yml`); um segundo
  workflow testa backup e restauração (`backup-restore-test.yml`).
- Os testes do leitor óptico usam cartões sintéticos desenhados com a geometria real
  (determinísticos, sem fotos no repositório).
