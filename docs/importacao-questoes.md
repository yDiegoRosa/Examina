# Importação de Questões — Formato de CSV / Excel

Especificação do arquivo de importação em lote do Banco de Questões (RF02).
O template oficial pode ser baixado direto no sistema em **Questões → Importar → Baixar modelo (CSV/Excel)**.

## Estrutura das colunas (15 colunas)

Ordem recomendada do cabeçalho (1ª linha do arquivo):

```
origem,questao,tipo,disciplina,topico,dificuldade,enunciado,opcao_a,opcao_b,opcao_c,opcao_d,opcao_e,gabarito,tags,comentarios
```

| Coluna | Obrigatória? | Valores aceitos / formato | Se ausente/vazia |
|---|---|---|---|
| `origem` | Não | Texto livre (ex.: `ENADE ADS 2021`) | vazio |
| `questao` | Não | Nº da questão na origem | vazio |
| `tipo` | Não | `OBJ`/`OBJETIVA` ou `DIS`/`DISCURSIVA` | inferido: com alternativas → `OBJ`, senão `DIS` |
| `disciplina` | **Sim** | Uma das 34 disciplinas da matriz, pelo nome ou pela sigla, sem diferenciar acentos/maiúsculas: `Segurança da Informação (SEG)`, `Segurança da Informação`, `SEG` ou `4SEG`. **A importação não cria disciplinas.** | linha rejeitada |
| `topico` | Não | Texto livre; criado dentro da disciplina | sem tópico |
| `dificuldade` | **Recomendada** | `F`/`M`/`D` (ou `FÁCIL`/`MÉDIO`/`DIFÍCIL`) | **`M` (Médio)** ⚠️ |
| `enunciado` | **Sim** | Texto; aceita quebras de linha | linha rejeitada |
| `opcao_a` … `opcao_e` | Condicional | Texto/número da alternativa | vazio (obrigatórias em objetivas) |
| `gabarito` | Condicional | Letra da correta: `A`–`E` | vazio (obrigatório em objetivas) |
| `tags` | Não | Uma ou mais, separadas por `,` ou `;` | sem tags |
| `comentarios` | Não | Justificativa (objetiva) / critérios de correção (discursiva) | vazio |

> ⚠️ **Por que classificar importa:** sem `dificuldade`/`tipo`/`topico`, a questão entra como *Médio* e
> sem tópico. Isso **passa** na importação, mas a geração de provas por máscara (inclusive o simulado
> ENADE) depende dessa classificação para selecionar questões. A disciplina é obrigatória: vazia ou fora
> da matriz (ex.: `Computação`) recusa a linha com o motivo, para a lista de disciplinas não ganhar
> duplicatas. Nome certo com a sigla errada (ex.: `Estrutura de Dados (EDA)`) é aceito pelo nome.

### Aliases de cabeçalho aceitos

O parser é tolerante a variações de nome (não diferencia maiúsculas/acentos):

- `questao` ≡ `questão`, `nº`, `numero_questao`
- `opcao_a` ≡ `opção a`, `opcao a`, `alt_a`, `alternativa a` (idem b–e)
- `gabarito` ≡ `correta`, `resposta`
- `comentarios` ≡ `comentários`, `justificativa`, `criterios_correcao`
- `imagem` ≡ `figura`, `arquivo_imagem` (só no envio por `.zip` — ver abaixo)

## Regras de ouro (importar sem erro)

1. **Cabeçalho na 1ª linha.** `enunciado` e `disciplina` são obrigatórios.
2. **Objetiva:** no mínimo **2 alternativas** preenchidas + `gabarito` apontando uma delas (`A`–`E`).
3. **Discursiva:** deixe `opcao_*` e `gabarito` **vazios**; use `comentarios` para os critérios de correção.
4. **Enunciado único:** o sistema rejeita duplicatas dentro do arquivo e contra o banco (ignora caixa e espaços).
5. **Delimitador** `,` **ou** `;` (detectado automaticamente). Salve em **UTF-8** (aceita BOM; latin-1 como fallback).
6. **Excel (.xlsx):** use a 1ª aba. Números viram texto (`26.0` → `26`). Linhas/colunas vazias ao redor são ignoradas.
7. **Importação transacional por linha:** as linhas válidas são gravadas e as inválidas são reportadas
   individualmente — uma linha ruim **não** derruba o lote inteiro.

## Exemplos

**Objetiva (CSV):**
```csv
origem,questao,tipo,disciplina,topico,dificuldade,enunciado,opcao_a,opcao_b,opcao_c,opcao_d,opcao_e,gabarito,tags,comentarios
ENADE ADS 2021,26,OBJ,Redes de Computadores,DNS,M,Qual a função principal do DNS?,Resolver nomes em IPs,Rotear pacotes,Criptografar tráfego,Balancear carga,Comprimir dados,A,redes; dns,O DNS traduz nomes de domínio em endereços IP.
```

**Discursiva (CSV):** — alternativas e gabarito vazios; critérios em `comentarios`:
```csv
ENADE ADS 2021,4,DIS,Engenharia de Software,Processos de Software,M,Explique as diferenças entre os modelos cascata e ágil.,,,,,,,engenharia-de-software; processos-ageis,Espera-se o contraste entre previsibilidade (cascata) e iteração/adaptação (ágil).
```

## Questões com imagem (envio por `.zip`)

Para questões com figura, envie um **`.zip`** com a planilha e uma pasta de imagens, e acrescente
à planilha a coluna **`imagem`** (16ª coluna). O modelo pronto está em
**Questões → Importar → Baixar exemplo com imagens (ZIP)**.

```
questoes_enade.zip
├── questoes.xlsx          (ou questoes.csv — uma única planilha no ZIP)
└── imagens/
    ├── 2021_tads_q10.png
    └── 2017_tads_q27.png
```

| Coluna `imagem` | Resultado |
|---|---|
| `imagens/2021_tads_q10.png` | a questão recebe essa figura |
| *(vazia)* | a questão entra sem imagem |
| `2021_tads_q10.png` | também vale: só o nome do arquivo, se ele for único no ZIP |

- **É o nome que liga a imagem à questão**, não a ordem dos arquivos na pasta. Maiúsculas e
  minúsculas não fazem diferença; `\` e `/` são aceitos.
- **Imagens:** PNG ou JPG de verdade (o conteúdo é conferido, não só a extensão), até
  `IMPORT_IMAGEM_MAX_SIZE_MB` (padrão **5 MB**) cada, **uma por questão**. Prefira só a figura
  recortada — a página inteira da prova ocupa o caderno todo. Alternativas em forma de imagem
  não são suportadas.
- **ZIP:** até `IMPORT_ZIP_MAX_SIZE_MB` (padrão **50 MB**). Pastas `__MACOSX`, arquivos ocultos e
  caminhos com `..` são ignorados.
- **Erros por linha:** se a imagem citada não estiver no ZIP, não for PNG/JPG válido ou passar do
  limite, só aquela questão é recusada (o relatório diz o motivo); as demais entram normalmente.
- A coluna `imagem` preenchida num `.xlsx`/`.csv` enviado **sem** ZIP recusa a linha (não há de
  onde tirar a imagem).
- No caderno (PDF), a figura sai abaixo do enunciado. Depois de importada, a imagem pode ser
  trocada no formulário da questão.
