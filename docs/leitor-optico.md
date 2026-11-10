# Cartão-resposta e leitor óptico (OMR)

Como o cartão é desenhado, como uma foto vira respostas corrigidas e o que fazer quando a leitura
falha. Código: `provas/services/answer_sheet_layout.py` (geometria), `fiducial.py` (marcadores e
QR), `folha_service.py` (PDF do cartão), `omr.py` (leitura) e `correcao.py` (gravação das notas).

## O cartão

- **Fonte única de geometria:** `answer_sheet_layout.py` define em milímetros (A4, origem no canto
  superior esquerdo) a posição de tudo. O mesmo objeto é usado para **imprimir** o cartão e para
  **ler** a foto, por isso as bolhas estão sempre onde o leitor espera. Versão do esquema: `fr-v1`.
- **4 marcadores ArUco** (`DICT_4X4_50`, IDs 0–3 nos cantos superior esquerdo, superior direito,
  inferior direito e inferior esquerdo), de 12 mm, com o centro a 14 mm da borda. Dão a orientação
  e permitem endireitar a foto.
- **QR** de 24 mm no canto superior direito, com o texto `EXM1F|<token>`. O token (UUID) é o
  `FolhaResposta.qr_token`: identifica aluno, aplicação e versão.
- **Grade de respostas:** só as questões **objetivas**, numeradas de 1 a N na ordem da versão,
  alternativas A–E, em até 4 colunas. Bolha de 2,4 mm de raio (1,9 mm nos cartões impressos antes
  de 06/10/2026, que continuam legíveis).
- O cartão é **nominal** (nome, matrícula e turma impressos); a matriz de matrícula para pintar
  existe na geometria, mas não é usada.

Não altere a geometria sem trocar o `SCHEMA_VERSION` e rever os testes: cartões já impressos
deixariam de ser lidos.

## Do envio à nota

1. **Envio** (tela "Enviar cartões" da aplicação): cada imagem vira uma `FolhaDigitalizada`
   **pendente**. O navegador reduz fotos grandes para no máximo 3200 px no lado maior (JPEG 0,88)
   antes de enviar; HEIC é recusado com orientação. Limite de 10 MB por imagem.
2. **Corrigir cartões (OMR)** processa as pendentes (`correcao.corrigir_folha_digitalizada`):
   1. **Identificação pelo QR:** tenta ler o QR na imagem inteira; se falhar, endireita a folha e
      tenta de novo só no recorte do QR (normal e ampliado 2×). Esse "plano B" resolve muitas
      fotos de celular. A folha precisa ser da mesma aplicação e não pode estar anulada.
   2. **Endireitamento:** acha os 4 marcadores e gera uma imagem A4 canônica de **10 px/mm**
      (2100 × 2970 px). Se não achar os 4 na foto original, tenta de novo reduzindo a imagem para
      1500, 1200, 1000 e 800 px de largura (fotos de 12 MP com ruído precisam disso).
   3. **Ajuste fino:** folha segurada na mão fica curvada e desloca as bolhas até ~2 mm, de modo
      diferente em cada coluna. Para cada questão, o leitor procura o anel impresso das bolhas até
      3 mm em volta da posição esperada e desloca a questão pela mediana (precisa de 2 anéis com
      correlação ≥ 0,45; senão mantém a posição).
   4. **Leitura das bolhas:** mede a fração de pixels escuros no disco interno de cada bolha
      (62% do raio, para não contar o anel impresso).
3. **Gravação:** cada questão objetiva vira um `Desempenho` automático, comparando com a letra
   correta **da versão do aluno**. A folha passa a "Lida" e a digitalização a "Processada".

## Como uma questão é classificada

| Situação | Regra | Grava |
|---|---|---|
| Marcada | a bolha mais preenchida tem fração ≥ `MIN_FILL` (0,35) | a letra; `acertou` conforme o gabarito |
| Em branco | nenhuma bolha chega a 0,35 | resposta vazia, conta como erro |
| Ambígua | a 2ª bolha também passa de 0,35 e tem ≥ `AMBIG_RATIO` (80%) da 1ª | `*`, conta como erro |

Brancos e ambíguas aparecem na **Revisão manual** da aplicação, onde o professor corrige à mão.
A correção manual nunca é sobrescrita por um novo processamento automático.

## Quando a leitura falha

| Mensagem | Causa provável | O que fazer |
|---|---|---|
| "QR não identificado" | QR cortado, borrado ou com reflexo | Nova foto mais nítida, cartão inteiro no quadro; ou vincular o cartão à mão na revisão manual |
| "Correção de perspectiva falhou" | algum dos 4 cantos fora da foto ou coberto | Fotografar com as 4 marcas pretas visíveis |
| "Imagem ilegível" | arquivo corrompido ou formato não suportado | Reenviar em JPG/PNG |
| "anulado" | o cartão foi anulado | Gerar cartão novo para o aluno |
| Muitas questões em branco | marcação fraca ou incompleta (lápis mediu só ~0,47 no teste de campo, perto do limite de 0,35) | Orientar caneta preta/azul e bolha preenchida inteira |

Boas fotos: luz uniforme, sem sombra da mão, celular paralelo à folha, folha apoiada em superfície
lisa, os 4 cantos aparecendo.

## Diagnóstico e calibração

O comando `diagnosticar_omr` lê fotos reais **sem gravar nada no banco** e mostra se o QR e os 4
marcadores foram achados e o preenchimento medido de cada bolha, com o resultado pelos limites
atuais:

```bash
python manage.py diagnosticar_omr foto1.jpg foto2.jpg
python manage.py diagnosticar_omr pasta/*.jpg --questoes 30          # quando o QR não é lido
python manage.py diagnosticar_omr foto.jpg --salvar-canonica saida/  # salva a imagem endireitada
```

No servidor, rode dentro do container `web` (ver [comandos.md](comandos.md)).

Os limites ficam em `provas/services/omr.py` (`MIN_FILL`, `AMBIG_RATIO`, `INNER_R_FRAC`,
`AJUSTE_*`). Antes de mudar qualquer um, rode o diagnóstico num conjunto de fotos reais e a suíte
de testes: `OmrLerRespostasTest` cobre folha curvada, bolha antiga e foto reduzida
(`test_foto_reduzida_le_igual` garante que a redução de 3200 px do navegador não muda a leitura).
