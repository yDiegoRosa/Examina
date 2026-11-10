# Acesso por perfil

Quem vê e quem altera o quê. As regras moram em `academico/acesso.py` (o "recorte" de cada
usuário) e `banco_questoes/permissions.py` (quem é quem e os decoradores das views).

## Perfis

| Perfil | Como a conta é marcada | Como é criada |
|---|---|---|
| **Administrador** | superusuário ou grupo `Administrador` | `createsuperuser`, ou no `/admin/` com o grupo criado por `manage.py criar_perfil_admin` |
| **Coordenador** | grupo `Coordenador` | no `/admin/` |
| **Professor** | grupo `Docente` | Acadêmico → Professores (login `nome.sobrenome`, senha temporária, troca obrigatória no 1º acesso) |

Os grupos `Docente` e `Coordenador` são criados por migration (`banco_questoes 0002`). Usuário
sem nenhum desses perfis não usa o sistema (recebe 403).

## O que cada um pode

| Área | Professor | Coordenador | Administrador |
|---|---|---|---|
| Questões | vê e altera **só as suas** | vê todas; altera só as suas | vê e altera todas |
| Importar questões | sim (ficam com ele como autor) | sim | sim, inclusive "em nome de" um professor |
| Transferir autoria em lote | — | — | sim |
| Máscaras e provas | só as suas; monta provas **só com as próprias questões** e só nas disciplinas que leciona | vê todas; altera as suas; usa o acervo inteiro | tudo |
| Simulado ENADE | — | — | só ele gera |
| Aplicações | as das suas provas, nas turmas em que leciona a disciplina | vê todas; aplica a própria prova em qualquer turma | tudo |
| Cartões, cadernos, correção, notas | nas aplicações das suas provas | vê; opera só as das suas provas | tudo |
| Acadêmico (alunos, turmas, matrículas, docências, professores) | só consulta as turmas em que leciona e os alunos delas | consulta | **só ele cadastra** |
| Desempenho | só os pares turma + disciplina das suas docências (inclusive o ENADE dessas turmas) | tudo | tudo |
| Arquivos (`/media/`) | fotos das aplicações que vê e imagens das suas questões/provas | todos | todos |

"Docência" = professor × disciplina × turma, cadastrada pelo administrador (junto com a turma ou
em Turmas → Docentes da turma). **Sem docência, o professor não vê turmas nem desempenho.**

## 404 e 403

- Objeto que o usuário **nem enxerga** (questão, prova ou aplicação de outro professor) responde
  **404**, sem revelar que existe. Vale também para arquivos de mídia.
- Objeto que ele **vê mas não pode alterar** (coordenador diante do que é de outro) responde
  **403**; as telas escondem os botões nesse caso.
- Área restrita ao perfil (ex.: Acadêmico → Professores para quem não é administrador) responde **403**.

## Como aplicar as regras no código

Sempre recorte o queryset pelas funções de `academico.acesso` e use `get_object_or_404` em cima
do recorte:

```python
from academico.acesso import questoes_visiveis, questoes_alteraveis

questao = get_object_or_404(questoes_visiveis(request.user), pk=pk)        # 404 se não vê
if not questoes_alteraveis(request.user).filter(pk=questao.pk).exists():
    raise PermissionDenied                                                  # 403 se vê mas não altera
```

| Função | Devolve |
|---|---|
| `questoes_visiveis`, `mascaras_visiveis`, `provas_visiveis`, `aplicacoes_visiveis` | tudo para quem "vê tudo"; senão só o que é do usuário |
| `questoes_alteraveis` | tudo para o administrador; senão só as do usuário |
| `autor_do_acervo` | `None` (acervo inteiro) ou o próprio professor — passado ao motor de montagem de provas |
| `disciplinas_permitidas`, `turmas_permitidas` | todas, ou as das docências ativas do usuário |
| `pode_aplicar(user, prova, turma)` | se o usuário pode cadastrar aquela aplicação |
| `filtro_turma_disciplina(...)` | `Q` dos pares turma + disciplina (desempenho); `None` = sem recorte |

Decoradores das views (`banco_questoes/permissions.py`): `docente_ou_coordenador_required`
(qualquer perfil do sistema), `administrador_required` (cadastros, ENADE, autoria),
`pode_editar_questao_required`. "Vê tudo" (`ve_tudo`) = administrador ou coordenador.

Nos templates, o context processor expõe `perfil_admin` e `perfil_ve_tudo` para mostrar ou
esconder botões. Esconder botão não é segurança: a view precisa checar de novo.

Os testes de referência estão em `banco_questoes/tests/test_acesso_professor.py`.
