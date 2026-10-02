# AgroSmart · Fase 2 — Arquitetura analítica

A Fase 2 acrescenta ao AgroSmart uma **camada analítica**: um pipeline Spark que consolida os laudos
emitidos pelo app e um **painel interativo** dentro da própria interface web. A Fase 1 (diagnóstico
por CNN e Gemini, histórico SQLite, exportação CSV/JSON) continua igual. A única integração é
aditiva: cada envio pela interface passa a registrar a **análise** em duas tabelas complementares do
SQLite (`analises` e `analise_laudos`); a tabela `laudos` não muda. A camada analítica só **lê** do
histórico e pode ser removida sem afetar o diagnóstico.

## Visão geral

```text
 Fase 1 (operacional)                         Fase 2 (analítica)
 ─────────────────────                        ──────────────────────────────────────────────
 servidor.py ──► dados/historico.db ──┬─────────────────────────────────────────────┐
   (CNN / Gemini)  laudos              │ somente leitura                             │ ao vivo,
                   + analises          │ (lote)                                      │ somente leitura
                   + analise_laudos    ▼                                             ▼
                                                                src/analitica_operacional.py
                                                                (reais, regras da Silver/Gold em Python)
                                                                             │
 ferramentas/gerar_dados_simulados ─► exportar_snapshot.py ─► dados/raw/<id_snapshot>/
   (dados/simulados/*.jsonl)                                   manifesto.json + laudos.jsonl
                                                                        │
                       ┌────────────────────────────────────────────────┤
                       ▼ Spark local                                    ▼ Databricks Free Edition
             ferramentas/executar_pipeline.py              notebooks/pipeline_fase2_databricks.ipynb
                       │       src/pipeline/transformacoes_spark.py (mesmo código)       │
                       │   Bronze ─► Silver (+ rejeitados) ─► Gold (4 tabelas)            │
                       ▼                                                                  ▼
             dados/processados/<id_execucao>/  ◄── importar_processados.py ◄── exec_<id>.zip
                 manifesto.json  analises.jsonl  laudos.jsonl  agregados.jsonl  qualidade.json
                 dados/processados/ATUAL.json (ponteiro para a execução servida)
                       │
                       ▼
             src/analitica.py  ◄───────────────────────────────────────────────┘
               reais ← fonte operacional · simulados ← pacote · todos = soma
                       │
                       ▼
             GET /api/analitica (servidor.py)  ──►  /dashboard (web/dashboard.*)
```

**Duas responsabilidades.** O Spark/Databricks é o pipeline em **lote** (SQLite/simulados → RAW →
Bronze → Silver → Gold → pacote). Uma análise real recém-feita **não** espera por ele: o painel lê os
reais ao vivo do SQLite, com as mesmas regras de normalização, laudo principal e reanálise, num módulo
Python puro. Um teste de paridade passa o mesmo histórico pelos dois caminhos e exige resultado
idêntico, campo a campo.

**Sem dupla contagem.** Cada origem tem uma única fonte: **Reais** → SQLite ao vivo; **Simulados** →
pacote processado; **Todos** → a soma. Laudos reais que estejam num pacote (de uma execução
`--origem ambos`, local ou no Databricks) são ignorados pela API, com aviso no painel. O pipeline
continua extraindo as análises reais quando um snapshot é tirado — elas ficam nas camadas
Bronze/Silver/Gold e no pacote, para auditoria e consultas, mas o painel usa a fonte ao vivo.

**O servidor FastAPI nunca inicializa o Spark.** Ele só lê o pacote processado — arquivos JSON Lines
pequenos — e funciona sem Java e sem PySpark. Um teste automatizado garante que `pyspark` e
`tensorflow` não são importados ao consultar o painel.

## Componentes

| Arquivo | Papel | Depende de Spark? |
|---|---|---|
| `src/pipeline/contratos.py` | Campos, tipos, domínios, caminhos e versão do schema | não |
| `src/pipeline/normalizacao.py` | Tabelas de sinônimos (cultura, diagnóstico) e regras de categoria | não |
| `src/pipeline/ingestao.py` | Leitura do SQLite (read-only) e dos simulados; snapshot RAW | não |
| `src/pipeline/transformacoes_spark.py` | Bronze, Silver, Gold e exportação do pacote | **sim** |
| `src/pipeline/pacote.py` | Publicação atômica, validação e ponteiro do pacote processado | não |
| `src/analitica.py` | Junta as fontes; filtros, cards, gráficos, tabela e qualidade para a API | não |
| `src/analitica_operacional.py` | Análises reais ao vivo do SQLite, no formato da Gold | não |
| `src/historico.py` | Tabelas `analises`/`analise_laudos`; `salvar_analise()` transacional | não |
| `servidor.py` | Rotas novas: `GET /api/analitica` e `GET /dashboard` | não |
| `web/dashboard.{html,css,js}` | Painel, sem framework e sem build | — |
| `ferramentas/gerar_dados_simulados.py` | Gerador reprodutível (semente fixa) | não |
| `ferramentas/exportar_snapshot.py` | Cria o snapshot RAW | não |
| `ferramentas/executar_pipeline.py` | Pipeline completo local | **sim** |
| `ferramentas/importar_processados.py` | Importa o `.zip` exportado pelo Databricks | não |
| `notebooks/pipeline_fase2_databricks.ipynb` | Pipeline no Databricks com tabelas Delta | (runtime) |

## Unidades: análise, laudo e imagem distinta

| Termo | Definição | Identificador |
|---|---|---|
| **Análise** | uma execução do AgroSmart sobre uma imagem | `id_analise` |
| **Laudo** | um resultado emitido por um motor (CNN ou VLM) | `id_laudo` |
| **Imagem distinta** | um conteúdo de imagem | `id_imagem` |

* No **modo comparação** uma análise tem **dois laudos**.
* Uma **reanálise** da mesma foto é uma análise nova, mas **não** uma imagem distinta nova.
* No **modo automático**, quando o CNN fica abaixo do limiar, só o laudo do VLM é emitido (como na
  Fase 1).

**Laudo principal.** Para que uma análise com dois laudos tenha uma única condição, vale a mesma
regra do modo automático: o laudo do CNN, se ele foi conclusivo; senão o do VLM, se ele foi
conclusivo; senão a análise é indeterminada. Os dois veredictos ficam em `condicao_cnn` e
`condicao_vlm`, e `concordancia_motores` diz se concordaram.

**Análises reais.** Cada envio pela interface principal grava, numa única transação, os laudos
(como antes), uma linha em `analises` e os vínculos em `analise_laudos`:

| Campo | Origem |
|---|---|
| `id_analise` | UUID4 gerado no envio |
| `id_imagem` | SHA-256 dos bytes recebidos (o mesmo arquivo de novo = reanálise, não imagem nova) |
| `modo` | modo pedido na interface |
| `limiar` | limiar efetivamente aplicado — nulo no modo só VLM ou se o CNN estava indisponível |
| `uf`, `municipio`, `propriedade`, `talhao` | opcionais, do bloco "Local da coleta"; UF validada entre as 27 siglas |

Uma comparação CNN/VLM é 1 análise + 1 imagem distinta + até 2 laudos. Remover um laudo remove o
vínculo; a análise que fica sem laudos sai junto.

**Laudos reais sem vínculo** — gravados antes desta versão, ou por outro caminho (como o app Streamlit,
que continua usando `historico.salvar`) — **não são agrupados por suposição**. Eles entram em
`gold_laudos`, na contagem de laudos, na distribuição por motor e na tabela, mas não nos indicadores
por análise.

**Identidade composta.** Laudo = `origem_dado + origem_instancia + id_laudo`; análise =
`origem_dado + origem_instancia + id_analise`; imagem = `origem_dado + id_imagem`. Ids iguais em
instalações ou origens diferentes nunca são fundidos.

**Unidade de cada parte do painel.** Cards e gráficos principais: uma linha representativa por
**análise**. Tabela: **laudos** — uma análise no modo comparação aparece duas vezes. As contagens não
precisam coincidir, e o painel diz isso logo acima dos cards.

## Camadas

### RAW — snapshot imutável

`dados/raw/<id_snapshot>/laudos.jsonl` (um laudo por linha, campos do contrato) e `manifesto.json`
(versão do schema, instante da extração, quantidade por origem, fontes e SHA-256 do arquivo). Não
contém imagens, miniaturas nem BLOBs. A pasta só recebe o nome definitivo depois que o manifesto foi
gravado: um snapshot interrompido nunca parece completo.

### Bronze — como chegou, com procedência

Leitura com **schema explícito** (montado a partir de `contratos.CAMPOS_RAW`) em modo `PERMISSIVE`:
uma linha que não é JSON válido não derruba o job; ela segue com o texto em `_registro_corrompido`.
Acrescenta `id_execucao`, `processado_em` e `versao_normalizacao`. Nada é corrigido ou descartado.

### Silver — limpa, normalizada, validada

* aparar texto; marcadores de vazio da Fase 1 (`—`, `-`, vazio) viram nulo;
* `data_analise` → timestamp com `try_to_timestamp` (aceita ISO com e sem fuso; sem fuso = horário de
  Brasília, pelo `spark.sql.session.timeZone`);
* condição (`saudavel` / `doente` / `indeterminado`), motor (`cnn` / `vlm`) e origem
  (`real` / `simulado`) reduzidos a códigos;
* cultura e diagnóstico normalizados por sinônimos, **preservando `diagnostico_original`**;
* `categoria_problema` ∈ {saudavel, doenca, praga, outra_anomalia, indeterminado};
* validação: `id_laudo`, data, origem, motor e condição obrigatórios; confiança em [0, 1]; **confiança
  numérica proibida em laudo do VLM** (a autoavaliação do Gemini não é probabilidade); limiar em (0, 1];
  versão de schema compatível;
* identidade por `origem_dado + origem_instancia + id_laudo`: cópias idênticas ficam uma só (a da
  extração mais recente; desempate determinístico) e as demais vão para os rejeitados como `duplicado`;
  mesma identidade com conteúdo diferente → todas rejeitadas como `conflito_identidade_laudo`, sem
  escolher um lado;
* `silver_rejeitados`: cada registro recusado com a lista de motivos e o registro original.

Toda a lógica usa a API de DataFrame. Não há UDF Python: as tabelas de `normalizacao.py` viram
expressões `CASE WHEN` nativas. As mesmas regras existem como funções Python de referência, e os
testes conferem os dois lados com os mesmos casos.

### Gold

| Tabela | Grão | Para quê |
|---|---|---|
| `gold_analises` | 1 linha por análise identificada | cards, condição, categorias, diagnósticos, evolução, localidade, cultura |
| `gold_laudos` | 1 linha por laudo válido | motor, tabela de laudos, laudos reais sem id de análise |
| `gold_agregados` | semana × origem × UF × município × cultura | séries prontas com **numerador e denominador** |
| `gold_qualidade` | origem × métrica | recebidos, rejeitados por motivo, campos ausentes, localidade ausente, identidade incompleta, comparações discordantes |

Percentuais agregados sempre carregam `n_*` (numeradores) e `n_analises` (denominador). Para
reagrupar, somam-se numeradores e denominadores; **nunca se tira média de percentuais**.

Uma análise cujos laudos discordam no contexto (imagem, arquivo, localidade, modo, limiar, tipo de
amostra) não entra em `gold_analises`; ela é contada em `gold_qualidade` como
`analise_contexto_conflitante`.

**RAW vazio é legítimo.** Zero registros produzem Bronze, Silver e Gold vazias e um pacote válido
com zero análises, zero laudos e zero agregados; publicado, ele substitui o anterior e o painel mostra
"sem dados".

### Pacote processado

`dados/processados/<id_execucao>/` com `analises.jsonl`, `laudos.jsonl`, `agregados.jsonl`,
`qualidade.json` e `manifesto.json`. Publicação atômica: os arquivos vão para uma pasta temporária, o
manifesto (com o SHA-256 de cada arquivo e `status: "completo"`) é gravado por último, a pasta é
renomeada e só então o ponteiro `ATUAL.json` é trocado com `os.replace`.

A validação (`pacote.ler`) confere: manifesto é objeto; tipos de `status`, `versao_schema`,
`id_execucao` e `arquivos`; presença, SHA-256 e quantidade de linhas de cada arquivo; estrutura
mínima de cada registro JSONL (objeto, campos obrigatórios, domínios de origem/condição/categoria/
motor, confiança em [0, 1] e nula no VLM); identidades únicas; contagens declaradas (`por_origem`,
`contagens`) e soma dos agregados. Todo defeito de arquivo externo vira `PacoteInvalidoError` — nunca
HTTP 500. A publicação roda a mesma validação antes de renomear a pasta.

A API lê e valida os quatro arquivos da **mesma** pasta numa única passada — nunca mistura execuções.
Se o pacote atual estiver malformado, ela usa a execução **válida** mais recente e mostra um aviso;
sem nenhum pacote válido, responde `disponivel: false` com o motivo.

`id_execucao` só aceita `exec_` + letras, dígitos, `_` e `-`, e o caminho resolvido precisa ficar
dentro de `dados/processados/`; isso vale para a publicação, para o ponteiro `ATUAL.json` e para a
importação. Cada publicação usa um nome temporário exclusivo.

## API analítica

Uma rota consolidada, para manter o servidor simples:

```
GET /api/analitica?origem=&inicio=&fim=&uf=&municipio=&propriedade=&talhao=&cultura=&condicao=&categoria=&motor=&pagina=
```

Devolve `pacote` (execução, data de processamento, snapshot), `origens` (contagens real/simulado),
`origem_padrao`, `opcoes` (valores de cada filtro, com localidade encadeada), `cards`, `graficos`,
`tabela` (25 laudos por página) e `qualidade`. Os cálculos usam o pacote completo — não o
`/api/historico`, que é limitado a 60 itens.

* Filtros de análise usam o **motor do laudo principal**; filtros de laudo usam o motor do próprio laudo.
* Validação estrita: datas só `AAAA-MM-DD` (normalizadas), `inicio` ≤ `fim`, e `origem`, `condicao`,
  `categoria` e `motor` só com os valores do contrato. Fora disso: HTTP 400 com a mensagem.
* O valor `__nao_informado__` filtra registros com o campo nulo (ex.: análises sem UF).
* **Origem padrão:** **Simulados** enquanto houver menos de 20 análises reais (poucas análises dariam
  gráficos quase vazios); a partir de 20, **Reais**. O botão "Reais" sempre mostra a contagem ao vivo,
  e o painel avisa quando há reais disponíveis. Real e simulado só se misturam quando o usuário
  escolhe "Todos", e o painel sinaliza isso.
* **"Ver no painel":** depois de uma análise salva, a tela principal oferece esse botão, que abre
  `/dashboard?origem=real&analise=<id>`. O painel confirma, numa faixa, que aquela análise entrou.

## Dashboard

`/dashboard`, acessível pelo menu "Painel analítico" e pelo botão na seção Histórico da página
inicial. Mesma identidade visual (vidro sobre verde, acento lima), HTML/CSS/JS sem dependências; os
gráficos são SVG e HTML gerados pelo próprio `dashboard.js`.

* **Cards:** análises identificadas, laudos, imagens distintas, % saudável, % doente, % indeterminado —
  cada percentual com "x de y análises"; denominador zero aparece como "sem dados".
* **Gráficos:** evolução semanal (colunas empilhadas por condição + linha da parcela doente), condição,
  categorias, diagnósticos mais frequentes, comparação por localidade, cultura × condição, motor
  (por laudo e por análise). Todos com dica ao passar o mouse.
* **Filtros:** período, origem, UF, município, propriedade, talhão, cultura, condição, categoria e
  motor. O estado fica na URL (dá para compartilhar um recorte). Só a consulta mais recente atualiza a
  tela: cada nova consulta cancela a anterior (`AbortController`) e respostas atrasadas são
  descartadas. Se a consulta falhar, os indicadores são ocultados e o erro aparece com um botão
  "Tentar de novo" — números de outro recorte nunca ficam à vista.
* **Transparência:** faixa amarela "DADOS SIMULADOS" ou azul "DADOS REAIS", data de processamento,
  execução e snapshot; painel de qualidade e cobertura; tabela de registros com o diagnóstico original
  quando difere do normalizado.
* **Cores:** saudável em azul e doente em laranja (o par verde/vermelho dos selos da Fase 1 é
  indistinguível para daltônicos: ΔE 5,2 no validador, contra 26,8 do par azul/laranja). Indeterminado
  em cinza neutro. A cor nunca é o único código: há legenda, rótulo e dica.
