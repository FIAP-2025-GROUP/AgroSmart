# AgroSmart · Fase 2 — Roteiro do vídeo (≈3 min)

Todos os números abaixo foram lidos do painel (`/dashboard`, origem **Simulados**) depois de gerar os
dados com a semente padrão e rodar o pipeline. Com a mesma semente, eles se repetem em qualquer
máquina; só o identificador da execução muda.

**Cuidado com a fala:** os gráficos mostram resultados dos **registros analisados** — nesta
demonstração, registros **simulados**. Não descreva os números como prevalência real, situação de uma
lavoura ou efeito de manejo.

**Preparação antes de gravar**

```bash
.venv\Scripts\python.exe ferramentas\gerar_dados_simulados.py
.venv\Scripts\python.exe ferramentas\executar_pipeline.py --origem simulado
.venv\Scripts\python.exe servidor.py
```

Deixe abertos: `http://localhost:8000`, `http://localhost:8000/dashboard`, o terminal com a saída
do pipeline e o notebook no Databricks já executado.

---

## 0:00–0:30 · Problema e AgroSmart

**Tela:** página inicial do AgroSmart; envie `dados/exemplos/tomate_requeima.jpg` no modo "Somente
CNN especializado".

> Na Fase 1, o AgroSmart diagnostica uma planta a partir de uma foto: um CNN treinado em 12 condições
> de 5 culturas e, para o resto, o Gemini. Aqui, a requeima do tomate saiu com 78% de confiança. Mas
> cada laudo responde só sobre uma foto. A Fase 2 consolida muitos laudos para mostrar em que
> culturas, locais e semanas os registros analisados apontam mais problemas — e com que qualidade de
> dados.

## 0:30–1:10 · Fonte dos dados

**Tela:** clique em "Painel analítico" no menu. Mostre a faixa amarela "DADOS SIMULADOS" e o seletor
Reais / Simulados / Todos.

> Os dados têm duas origens, sempre separadas. A real é o histórico SQLite do próprio app: os laudos
> reais aparecem como laudos, mas a Fase 1 ainda não registra identificador de análise, de imagem nem
> localidade, e os indicadores por análise dependem disso. Por isso a demonstração usa um conjunto
> **simulado**, gerado com semente fixa a partir
> das 12 classes reais do modelo: 12 semanas, 6 propriedades, e também café, soja e feijão, que só o
> Gemini reconhece. Ele está marcado em amarelo e não representa a situação de nenhuma lavoura. São 1.321
> registros; o pipeline aceitou 1.311 laudos, de **1.157 análises** sobre **1.067 imagens distintas**.

**Tela:** aponte os cards Análises / Laudos / Imagens distintas.

> Análise, laudo e imagem são coisas diferentes. No modo comparação, uma análise gera dois laudos. Uma
> foto reenviada é uma análise nova, mas não uma imagem nova: foram 53 reanálises. Os cards e gráficos
> contam análises; a tabela no fim da página lista laudos.

## 1:10–1:50 · Pipeline Spark / Databricks

**Tela:** o terminal com a saída do `executar_pipeline.py`; depois o notebook no Databricks (seções
Silver e Gold e o catálogo com as tabelas Delta).

> O app exporta um snapshot RAW, com manifesto e hash. O Spark lê com schema explícito e grava a Bronze
> como chegou. Na Silver, normaliza datas, culturas e diagnósticos: "Míldio tardio", escrito pelo
> Gemini, vira Requeima, mas o texto original continua guardado. Ela também valida e deduplica:
> **10 registros foram rejeitados**, cada um com o motivo (confiança fora de 0 a 1, data impossível,
> motor desconhecido e 3 duplicados), sem derrubar o lote. A Gold gera quatro tabelas: análises,
> laudos, agregados semanais com numerador e denominador, e qualidade. O mesmo código roda localmente
> e no Databricks Free Edition, onde as camadas são tabelas Delta. O resultado é um pacote validado
> que o painel lê. O servidor web não sobe o Spark.

## 1:50–2:40 · Dashboard e insights

**Tela:** cards gerais.

> Nos registros simulados, 70,6% das análises saíram saudáveis, 26,5% doentes e 2,9%
> indeterminadas. Cada percentual mostra a conta: 306 de 1.157.

**Tela:** filtro UF = PR, Cultura = Milho. Passe o mouse na primeira e na última coluna.

> Nos registros de milho do Paraná, a parcela de análises doentes sobe: 1 em 15 na primeira semana
> de junho, 7 em 13 na semana de 17 de agosto, ou 54%. A linha mostra essa parcela semana a semana,
> sempre dividindo pelo total da própria semana.

**Tela:** Cultura = Tomate; período de 29/06 a 19/07 e depois a partir de 03/08. Mostre
"Diagnósticos mais frequentes" nos dois recortes.

> No tomate, o diagnóstico mais frequente muda. Entre o fim de junho e meados de julho, 18 das 20
> análises doentes são requeima, uma doença. Em agosto, o ácaro-rajado, uma praga, responde por 15 de 23.

**Tela:** Cultura = Uva.

> Na uva, a parcela de análises doentes cai: 6 de 13 na primeira semana, 1 de 23 na última.

**Tela:** limpe os filtros e role até "Distribuição por motor" e o texto acima dos gráficos.

> Os dois motores se complementam: 873 laudos do CNN e 438 do Gemini. Nas 114 comparações em que os
> dois deram veredito, discordaram em 7.

## 2:40–3:10 · Atualização e conclusão

**Tela:** painel "Qualidade e cobertura" e a linha "Atualizado em … · execução …".

> A transparência faz parte do painel: 93,3% das análises têm localidade completa, 96,8% têm
> identificação da imagem e 0,8% dos registros foram rejeitados. A origem dos dados e a data de
> atualização ficam sempre visíveis. Para atualizar, basta rodar o pipeline de novo, localmente ou
> no Databricks: ele publica um pacote novo, validado, e o painel passa a mostrá-lo no próximo
> carregamento. Assim o AgroSmart passa a consolidar os laudos de muitas fotos ao longo do tempo,
> deixando sempre claro de onde vêm os dados e o que eles medem.

---

### Números usados (conferência)

| Fala | Valor no painel | Onde |
|---|---|---|
| registros recebidos / rejeitados | 1.321 / 10 (0,8%) | Qualidade |
| laudos · análises · imagens distintas | 1.311 · 1.157 · 1.067 | Cards |
| reanálises | 53 | Nota abaixo dos cards |
| saudável · doente · indeterminado | 70,6% (817) · 26,5% (306) · 2,9% (34) de 1.157 | Cards |
| milho PR, 1ª × última semana | 1/15 → 7/13 (53,8%) | Evolução, filtro UF=PR + Milho |
| tomate 29/06–19/07 | requeima 18 de 20 doentes | Diagnósticos, filtro Tomate + período |
| tomate a partir de 03/08 | ácaro-rajado 15 de 23 doentes | Diagnósticos, filtro Tomate + período |
| uva, 1ª × última semana | 6/13 → 1/23 | Evolução, filtro Uva |
| laudos por motor | CNN 873 · Gemini 438 | Distribuição por motor |
| comparações discordantes | 7 de 114 com veredito dos dois | Nota abaixo dos cards |
| localidade completa · id de imagem | 93,3% · 96,8% | Qualidade |
| requeima no exemplo da Fase 1 | 78,3% | Laudo do `tomate_requeima.jpg` |
