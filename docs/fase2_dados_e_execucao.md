# AgroSmart · Fase 2 — Dados e execução

Guia prático: de onde vêm os dados, o que cada campo significa e como rodar tudo, localmente ou no
Databricks. A arquitetura está em [fase2_arquitetura.md](fase2_arquitetura.md).

## 1. Dados reais × simulados

| | Real | Simulado |
|---|---|---|
| Origem | histórico SQLite da Fase 1 (`dados/historico.db`) | `ferramentas/gerar_dados_simulados.py` |
| `origem_dado` | `real` | `simulado` |
| Passa pelo SQLite? | sim — é de onde vem | **não**: entra direto pela fronteira RAW |
| Identidade | envios pela interface: análise (UUID), imagem (SHA-256), modo, limiar e localidade opcional; laudos antigos ou sem vínculo: só `id_laudo` | completa: análise, laudo, imagem, localidade |
| No painel | **ao vivo**, direto do SQLite, sem Spark | pelo pacote processado |
| Uso | o que o app realmente diagnosticou | demonstração do pipeline e do painel |

Os dados simulados **não representam incidência real** de doenças em nenhuma propriedade ou
município; os nomes de lugares só dão contexto geográfico ao exemplo. O painel mostra uma faixa
amarela "DADOS SIMULADOS" sempre que eles estão na tela e nunca os mistura com os reais sem que o
usuário escolha "Todos".

### O gerador

* Semente fixa (`20260901`): rodar de novo gera o mesmo arquivo, byte a byte.
* 12 semanas (01/06/2026 a 23/08/2026), 6 propriedades em 6 UFs, culturas do catálogo do CNN (tomate,
  batata, milho, uva, maçã) e fora dele (café, soja, feijão — só o VLM diagnostica).
* Laudos do CNN usam **somente as 12 classes de `src/rotulos.py`**, com os mesmos textos de sintomas e
  manejo, a regra do limiar e `confianca_texto` no formato da Fase 1. Laudos do VLM têm `confianca`
  nula e autoavaliação em palavras, com o diagnóstico em texto livre e variações de grafia.
* Modos de análise na proporção aproximada: automático 70%, comparação 15%, só CNN 10%, só VLM 5%.
* Tendências embutidas, para o painel ter o que mostrar: requeima no tomate que sobe e cede em
  julho, ácaro-rajado que aparece em agosto, ferrugem comum crescendo no milho de Cascavel, pinta-preta
  subindo na batata, podridão-negra caindo na uva.
* Imperfeições de propósito: ~6% de reanálises da mesma imagem, ~6% de análises com localidade
  incompleta, ~3% sem `id_imagem`, 7 registros inválidos e 3 duplicados, que a Silver rejeita com motivo.

Resultado com a semente padrão: **1.321 linhas → 1.311 laudos válidos, 1.157 análises, 1.067 imagens
distintas, 10 rejeitados**.

## 2. Dicionário de dados

### Laudo (RAW → Silver → `gold_laudos`)

| Campo | Tipo | Significado | Ausente quando |
|---|---|---|---|
| `versao_schema` | texto | versão do contrato (`2.0.0`) | — |
| `origem_instancia` | texto | instalação que gerou o laudo (`agrosmart-local`, `gerador-simulado`) | — |
| `id_analise` | texto | execução sobre uma imagem | todos os laudos reais (Fase 1) |
| `id_laudo` | texto | resultado de um motor — **obrigatório**; único dentro de `origem_dado + origem_instancia` | nunca (senão é rejeitado) |
| `id_imagem` | texto | identidade do conteúdo da imagem | laudos reais; alguns simulados |
| `arquivo` | texto | nome do arquivo enviado | — |
| `data_analise` | texto ISO → timestamp | momento do laudo; sem fuso = horário de Brasília | nunca (senão é rejeitado) |
| `fuso_origem` | texto | fuso declarado na origem | laudos reais |
| `uf`, `municipio`, `propriedade`, `talhao` | texto | localidade | laudos reais; parte dos simulados |
| `modo_solicitado` | texto | modo escolhido na Fase 1 (automático, só CNN, só VLM, comparação) | laudos reais |
| `limiar_cnn` | número | limiar de confiança do CNN usado | laudos reais; modo só VLM |
| `origem_dado` | `real` / `simulado` | procedência | nunca (senão é rejeitado) |
| `tipo_amostra` | `foto_campo` / `imagem_referencia` | natureza da foto | laudos reais |
| `especie_original` | texto | espécie como o motor escreveu | CNN indeterminado |
| `cultura` | texto | cultura normalizada (ex.: "Tomateiro (Solanum lycopersicum)" → Tomate) | CNN indeterminado |
| `condicao` | `saudavel` / `doente` / `indeterminado` | condição normalizada | nunca |
| `diagnostico_original` | texto | diagnóstico exatamente como emitido | — |
| `diagnostico_normalizado` | texto | nome canônico (ex.: "Míldio tardio" → Requeima) | — |
| `categoria_problema` | `saudavel` / `doenca` / `praga` / `outra_anomalia` / `indeterminado` | tipo do problema | — |
| `agente_original` | texto | agente causal como emitido | — |
| `motor` | `cnn` / `vlm` | motor que emitiu o laudo (`motor_original` guarda o rótulo da Fase 1) | nunca |
| `modelo_versao` | texto | versão do modelo | — |
| `confianca` | número 0–1 | probabilidade do CNN; **sempre nula no VLM** | VLM |
| `confianca_texto` | texto | confiança legível ("87.3%", "alta (autoavaliada)") | — |
| `sintomas`, `manejo`, `observacoes` | texto | texto do laudo | — |
| `cultura_mapeada`, `diagnostico_mapeado` | booleano | se o texto casou com um sinônimo conhecido | — |
| `data_dia`, `semana` | data | dia e segunda-feira da semana (fuso de Brasília) | — |
| `id_snapshot`, `extraido_em` | texto | snapshot RAW de origem | — |
| `id_execucao`, `processado_em`, `versao_normalizacao` | texto | execução do pipeline | — |

### Análise (`gold_analises`)

Campos de localidade, cultura, condição, diagnóstico e categoria vêm do **laudo principal**, mais:
`motor_principal`, `id_laudo_principal`, `n_laudos`, `tem_cnn`, `tem_vlm`, `comparacao`,
`condicao_cnn`, `condicao_vlm`, `concordancia_motores` (nulo se algum dos dois foi indeterminado),
`confianca_cnn` e `e_reanalise` (nulo quando não há `id_imagem`).

### Agregado (`gold_agregados`)

Chave `semana, origem_dado, uf, municipio, cultura`; contagens `n_analises` (denominador),
`n_saudavel`, `n_doente`, `n_indeterminado`, `n_doenca`, `n_praga`, `n_outra_anomalia`; e
`pct_saudavel`, `pct_doente`, `pct_indeterminado` da própria linha.

### Qualidade (`gold_qualidade`)

Formato longo `origem_dado, metrica, valor, denominador, unidade`. Métricas: `registros_recebidos`,
`rejeitados`, `rejeitado:<motivo>`, `laudos_validos`, `ausente:<campo>`, `localidade_ausente`,
`identidade_incompleta`, `laudo_sem_analise`, `diagnostico_nao_mapeado`, `analises_identificadas`,
`analise_localidade_ausente`, `analise_sem_id_imagem`, `reanalises`, `comparacoes`,
`comparacoes_discordantes`, `analise_contexto_conflitante`.

Motivos de rejeição: `json_invalido_ou_tipo_incompativel`, `versao_schema_incompativel`,
`id_laudo_ausente`, `data_ausente`, `data_invalida`, `origem_dado_invalida`, `motor_desconhecido`,
`condicao_invalida`, `confianca_fora_do_intervalo`, `confianca_numerica_em_laudo_vlm`,
`limiar_invalido`, `duplicado`, `conflito_identidade_laudo`.

### Identidade e duplicatas

* **Laudo** = `origem_dado + origem_instancia + id_laudo`. **Análise** = `origem_dado +
  origem_instancia + id_analise`. **Imagem** = `origem_dado + id_imagem`. O mesmo `id_laudo` vindo de
  outra instalação ou de outra origem (real × simulado) é outro registro, nunca uma duplicata.
* **Mesma identidade, mesmo conteúdo** (campos do contrato, ignorando `id_snapshot`/`extraido_em`):
  fica um registro; desempate determinístico pela extração mais recente e, depois, pelo maior
  `id_snapshot`. As cópias vão para os rejeitados como `duplicado`.
* **Mesma identidade, conteúdo diferente:** nenhum lado é escolhido. Todos vão para os rejeitados
  como `conflito_identidade_laudo` e aparecem na qualidade.
* **Análise com contexto conflitante:** se os laudos de uma mesma análise discordam em imagem,
  arquivo, localidade, modo, limiar ou tipo de amostra, a análise não é montada em `gold_analises`
  (os laudos continuam em `gold_laudos`) e é contada em `analise_contexto_conflitante`.
* O gerador simulado inclui a semente nos ids (`sim-<semente>-an-…`, `sim-<semente>-ld-…`), para
  que conjuntos de sementes diferentes possam ser combinados sem colisão.

## 3. Instalação

```bash
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt         # app (inclui FastAPI)
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt     # + testes
.venv\Scripts\python.exe -m pip install -r requirements-spark.txt   # + pipeline Spark local
```

O painel **não precisa** de `requirements-spark.txt` nem de Java.

O pipeline Spark local usa **PySpark 3.5 + Java 17** — é a configuração documentada e testada.
1. Instale um JDK 17 (por exemplo, o Eclipse Temurin 17).
2. Aponte `JAVA_HOME` para a pasta de instalação, sem o `\bin` no fim:

   ```bat
   setx JAVA_HOME "C:\Program Files\Eclipse Adoptium\jdk-17.0.x-hotspot"
   ```

   (abra um terminal novo depois do `setx`) ou, só para a sessão do PowerShell:
   `$env:JAVA_HOME = "C:\Program Files\Eclipse Adoptium\jdk-17.0.x-hotspot"`.
3. Instale o PySpark: `.venv\Scripts\python.exe -m pip install -r requirements-spark.txt`.
4. Confira: `"%JAVA_HOME%\bin\java" -version` deve mostrar `17`.

O `executar_pipeline.py` lê o `JAVA_HOME` (ou o `java` do PATH), recusa seguir sem Java e avisa se a
versão encontrada não for a 17. Outras versões não são suportadas nesta configuração.

No Windows, o Spark local grava as cópias de Bronze/Silver/Gold em `dados/{bronze,silver,gold}` como
JSON Lines, porque gravar Parquet exige o `winutils.exe` do Hadoop. Em Linux/macOS, ou com
`HADOOP_HOME` definido, a gravação é em Parquet. O processamento é o mesmo nos dois casos.

## 4. Execução local

```bash
# 1. Dados simulados (reprodutível: mesma semente, mesmo arquivo)
.venv\Scripts\python.exe ferramentas\gerar_dados_simulados.py

# 2. Snapshot RAW (opcional: o passo 3 cria um se você não informar --snapshot)
.venv\Scripts\python.exe ferramentas\exportar_snapshot.py --origem ambos

# 3. Pipeline completo: RAW → Bronze → Silver → Gold → pacote processado (marcado como atual)
.venv\Scripts\python.exe ferramentas\executar_pipeline.py
.venv\Scripts\python.exe ferramentas\executar_pipeline.py --origem simulado
.venv\Scripts\python.exe ferramentas\executar_pipeline.py --snapshot dados\raw\snap_...

# 4. App + painel
.venv\Scripts\python.exe servidor.py
#    http://localhost:8000            diagnóstico (Fase 1)
#    http://localhost:8000/dashboard  painel analítico (Fase 2)
```

Saída típica do passo 3 (≈40 s, a maior parte na subida da JVM):

```
[RAW]    snap_...: 1321 laudos {'simulado': 1321}
[BRONZE] 1321 registros
[SILVER] 1311 válidos · 10 rejeitados
[GOLD]   1157 análises · 1311 laudos · 201 agregados
[PACOTE] dados\processados\exec_... (marcado como atual)
```

### Atualizar o painel

Rodar de novo o passo 3 tira um snapshot novo, processa e publica uma execução nova, que o dashboard
mostra no próximo carregamento, sem reiniciar o servidor. Se a origem estiver vazia, a execução nova
também é vazia e o painel passa a mostrar "sem dados" — ele nunca continua exibindo o pacote anterior
só porque o novo não tem registros. As execuções antigas ficam em `dados/processados/` e podem voltar
a ser a atual com `pacote.marcar_atual(id_execucao)`.

**O que acontece com uma análise real feita agora.** Ao salvar, a tela principal grava a análise
(UUID, SHA-256 da imagem, modo, limiar aplicado e o "Local da coleta", se preenchido) e mostra o botão
**Ver no painel**. O painel lê os dados reais **ao vivo** do SQLite: a análise já aparece na visão
Reais, em todos os indicadores, sem rodar o Spark — o pipeline não precisa ser executado para isso.
Quando um snapshot for tirado depois, o Spark extrai essas mesmas análises com toda a identidade.

Para não contar duas vezes, a visão Reais usa só o SQLite; laudos reais presentes num pacote são
ignorados (com aviso). O painel abre em **Simulados** enquanto houver menos de 20 análises reais, e em
**Reais** a partir daí. Laudos reais **sem vínculo** de análise (gravados antes desta versão ou pelo
app Streamlit) continuam fora dos indicadores por análise.

## 5. Execução no Databricks Free Edition

1. Crie uma conta no Databricks Free Edition (compute **serverless**).
2. *Workspace → Create → Git folder*: clone o repositório do AgroSmart.
3. Abra `notebooks/pipeline_fase2_databricks.ipynb` dentro da Git folder e rode a seção **1**. Ela cria o
   schema `workspace.agrosmart` e o Volume `workspace.agrosmart.dados`.
4. No computador, gere o snapshot (`exportar_snapshot.py --origem ambos`). Em *Catalog → workspace →
   agrosmart → dados*, crie a pasta `raw/<id_snapshot>/` e envie `manifesto.json` e `laudos.jsonl`.
   *Atalho para demonstração:* `GERAR_SIMULADOS = True` na seção 3 gera os simulados no próprio
   Databricks.
5. *Run all*. O notebook grava as tabelas Delta `bronze_laudos` (append), `silver_laudos`,
   `silver_rejeitados`, `gold_analises`, `gold_laudos`, `gold_agregados` e `gold_qualidade`, roda as
   consultas e exporta `exportacoes/exec_<id>.zip` no Volume.
6. Baixe o `.zip` pelo Catalog Explorer e importe-o no computador:

```bash
.venv\Scripts\python.exe ferramentas\importar_processados.py exec_20261001T....zip
```

O importador valida o pacote antes e depois da cópia, recusa um zip adulterado ou com caminhos
suspeitos e marca a execução como atual. Se já existir uma execução com o mesmo id: conteúdo idêntico
→ importação idempotente; conteúdo diferente → erro de colisão, sem alterar nada nem trocar o pacote
atual. Recarregue o `/dashboard`.

O notebook não acessa o SQLite nem o `localhost`, não usa RDD, `SparkContext` nem DBFS legado, e
não chama `cache()` (não suportado no serverless): cada camada é gravada como tabela Delta e relida.

## 6. Testes

```bash
.venv\Scripts\python.exe -m pytest
```

| Arquivo | Cobre | Precisa de Spark? |
|---|---|---|
| `testes/test_ingestao.py` | contrato, histórico SQLite real (somente leitura, sem BLOB, sem identidade inventada), snapshot e hash, origem simulada, normalização, gerador reprodutível, fiel ao catálogo e sem colisão de ids entre sementes | não |
| `testes/test_pipeline_spark.py` | schema explícito, Bronze, rejeitados com motivo, deduplicação determinística, conflito de identidade, ids iguais em origens diferentes, contexto conflitante, sementes combinadas, normalização, datas, laudo principal, comparação, reanálise, agregados, qualidade, RAW vazio de ponta a ponta, publicação e leitura pela API | sim (o módulo inteiro é pulado sem PySpark; os testes que usam a sessão, sem Java) |
| `testes/test_analises_reais.py` | envio pela interface (dublê do diagnóstico): 1 análise + 1 imagem + 2 laudos na comparação, SHA-256, limiar efetivo, localidade validada, transação, cascata, banco antigo, extração com vínculo, painel ao vivo | não |
| `testes/test_api_analitica.py` | cards com numerador/denominador, "sem dados", origem padrão, real × simulado, filtros e enums estritos, datas AAAA-MM-DD, intervalo invertido, paginação, evolução sem média de percentuais, manifesto/arquivos/JSONL malformados sem HTTP 500, fallback só para pacote válido, path traversal, importação idempotente e colisão, servidor sem Spark/TensorFlow | não |

Dois cenários:

* **Instalação comum** (`requirements-dev.txt`, sem PySpark): os testes sem Spark rodam e
  `test_pipeline_spark.py` aparece como *skipped*.
* **Ambiente Spark** (`requirements-spark.txt` + `JAVA_HOME` no Java 17): todos rodam.

Os testes usam a fixture `testes/fixtures/raw_basico/laudos.jsonl` e pastas temporárias. Não tocam o
banco operacional, o pacote processado real, o Gemini nem o modelo CNN.

## 7. Limitações

* **Laudos reais sem vínculo.** Laudos gravados antes do registro de análises, ou pelo app Streamlit,
  não têm `id_analise`: aparecem como laudos, mas não entram nos indicadores por análise.
* **Duas implementações das regras.** A visão Reais aplica em Python as mesmas regras da Silver/Gold
  do Spark. O teste de paridade (`test_paridade_spark_e_fonte_operacional`) garante resultado idêntico;
  ao mudar uma regra no Spark, ele aponta a divergência.
* **Identidade da imagem por bytes.** O SHA-256 é dos bytes recebidos: a mesma foto recomprimida ou
  recortada conta como outra imagem.
* **Fuso.** Datas reais sem fuso são lidas como horário de Brasília (UTC−3), nos dois caminhos.
* **Dados simulados.** As tendências da visão "Simulados" foram desenhadas no gerador. Elas mostram
  o funcionamento do pipeline e do painel; não representam prevalência real, a situação de nenhuma
  lavoura nem efeito de manejo.
* **O que os gráficos medem.** Resultados dos registros analisados (a parcela de análises com cada
  condição), não a incidência no campo: depende de quais fotos foram enviadas.
* **Normalização por sinônimos.** O texto livre do VLM só é agrupado quando casa com um sinônimo
  conhecido; o resto mantém o texto original (e entra em "diagnóstico sem sinônimo conhecido" na
  qualidade). Novos sinônimos vão em `src/pipeline/normalizacao.py`.
* **Escala.** A API carrega o pacote em memória. É adequado para dezenas de milhares de laudos; para
  volumes muito maiores, o painel deveria consultar agregados pré-calculados.
* **Databricks não testado nesta máquina.** O código do notebook foi executado de ponta a ponta com
  Spark local (sem Delta). Os pontos específicos do Databricks (Volumes, `saveAsTable` em Delta,
  serverless) seguem a documentação, mas precisam ser conferidos na primeira execução real.
* **Windows sem Hadoop nativo.** As cópias locais das camadas saem em JSON Lines, não em Parquet.

## 8. Entrega acadêmica

```bat
.venv\Scripts\python.exe ferramentasxecutar_pipeline.py --origem simulado
.venv\Scripts\python.exe ferramentasmpacotar_entrega.py
```

O zip (`entrega/..._fase2_atividade.zip`) leva código, painel, notebook, documentação, testes,
gerador, `dados/simulados/` e **um** pacote processado: o apontado por `ATUAL.json`, que precisa
passar na validação e ser **exclusivamente simulado** — caso contrário o empacotamento é recusado.
Nunca entram: `.env`, `historico.db` ou qualquer SQLite, `dados/campo/`, snapshots RAW, camadas
Bronze/Silver/Gold, exportações, caches e temporários. O script verifica o zip gerado e aponta
qualquer item proibido.
