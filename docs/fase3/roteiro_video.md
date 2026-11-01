# AgroSmart · Fase 3 — Roteiro do vídeo (≈3 min 30 s)

O enunciado pede **2 a 4 minutos** mostrando: (1) a lógica criada, (2) a automação funcionando e (3) como o
sistema reagiria num caso real. Se possível, dividam as falas entre os integrantes (marcadas como **[A]**,
**[B]** e **[C]**; ajustem ao tamanho do grupo).

**Cuidado com a fala:** os dados são **simulados**. Digam "no cenário simulado", nunca "na fazenda X aconteceu".

## Preparação (antes de gravar)

Três terminais lado a lado e o navegador aberto em `http://localhost:8000/automacao`.

```bat
:: terminal 1 — monitor (zera o estado e fica rodando)
.venv\Scripts\python.exe ferramentas\monitor_fase3.py --reiniciar --intervalo 3

:: terminal 2 — painel
.venv\Scripts\python.exe servidor.py

:: terminal 3 — deixe digitado, mas só aperte Enter na hora certa
.venv\Scripts\python.exe ferramentas\gerar_dados_fase3.py --continuo --intervalo 5
```

Com `--intervalo 5` o cenário inteiro leva cerca de 2 min 30 s. Para um vídeo mais curto, use `--intervalo 3`
(cerca de 1 min 30 s).

---

## 0:00–0:25 · [A] Contexto

**Tela:** página `/automacao` vazia ("Sem dados ainda") e o terminal do monitor com "aguardando dados novos...".

> Nas fases anteriores o AgroSmart diagnosticava plantas por foto e montava um painel analítico. Na Fase 3 o
> sistema passa a **agir sozinho**. Laudos de diagnóstico e leituras de sensores de campo chegam numa pasta
> monitorada, cinco regras de decisão avaliam cada talhão e as ações saem automaticamente: alerta, relatório,
> e-mail ao agrônomo e um simulador de irrigação.

## 0:25–1:05 · [B] A lógica de decisão

**Tela:** role até a tabela "Regras de decisão" no fim da página (ou mostre a página 2 do PDF).

> As regras usam lógica booleana. A R1 diz: **se** houver pelo menos 5 análises no talhão nos últimos 7 dias
> **e** mais de 30% forem de planta doente **e não** houver alerta recente, **então** emite alerta de
> infestação, gera relatório e manda e-mail. A R2 é a do enunciado, a mesma praga ou doença em 3 dias seguidos,
> e aciona o plano de controle. A R3 controla a irrigação com histerese: liga abaixo de 25% de umidade do solo
> se não choveu, desliga acima de 35% **ou** com chuva. A R4 antecipa a requeima pelo clima úmido e ameno, e a
> R5 cruza a armadilha de pragas com os laudos. Todas têm um **NOT** de carência para não repetir o mesmo alerta.

## 1:05–2:25 · [C] A automação funcionando

**Tela:** aperte Enter no terminal 3. Deixe o painel visível; aponte o terminal do monitor de vez em quando.

> O gerador está publicando os dados dia a dia, como se viessem do campo. Reparem no monitor: a cada arquivo
> novo ele abre um lote e lê **só as linhas novas**. O CSV de sensores ganha linhas às 6h, 12h e 18h, e ele lê
> 4 de cada vez, não o arquivo inteiro.

Quando aparecer cada evento, comente (a ordem é sempre esta):

| No painel | Fala |
|---|---|
| R4 · MÉDIA no T02 (dia 27/10) | "Umidade do ar acima de 90% com clima ameno: risco de requeima, antes de a doença aparecer." |
| T01 **IRRIGANDO** (dia 28/10) | "Solo da batata abaixo de 25% e sem chuva: a irrigação ligou sozinha." |
| R1 · ALTA (dia 29/10) | "7 de 22 análises doentes, 32%: alerta de infestação." → **clique no botão ✉️ e-mail** e mostre o conteúdo, depois **📋 relatório**. |
| T01 desliga (dia 29/10) | "Umidade recuperou para 36%: desligou." |
| R2 plano de controle (dia 30/10) | "Requeima no terceiro dia seguido: plano de controle acionado." |
| R3 · CRÍTICA (dia 31/10) | "Solo em 14,6%: nível crítico, além de ligar a irrigação manda e-mail." |
| Gleba 2 sem alertas | "A Fazenda Santa Clara é o grupo de controle: dados normais, nenhum alerta." |

## 2:25–2:55 · [A] Prova de que não reprocessa

**Tela:** terminal 3.

```bat
.venv\Scripts\python.exe ferramentas\gerar_dados_fase3.py --reenviar 4
```

> Agora reenviamos o dia 29 com outro nome de arquivo, o mesmo conteúdo. O monitor lê, reconhece os 31
> registros como **já processados** e não dispara nenhuma ação: o contador de alertas continua em 12. Isso vem
> do checkpoint por arquivo e da chave única de cada registro, guardados no SQLite. Mesmo reiniciando o monitor,
> ele continua de onde parou.

(Opcional: Ctrl+C no monitor e rode de novo sem `--reiniciar`: a saída é "aguardando dados novos...".)

## 2:55–3:30 · [B] Caso real e fechamento

**Tela:** cards "Talhões agora" do painel.

> Num caso real, o produtor de tomate em Campinas tira fotos durante a semana pelo app, e as estações de campo
> mandam as leituras. Antes de a requeima se espalhar, o AgroSmart avisa pelo clima, detecta a infestação
> acima de 30%, manda o relatório para o agrônomo com o manejo indicado e aciona o plano de controle quando a
> doença persiste, enquanto cuida da irrigação da batata ao lado. Escolhemos micro-batch porque as regras de
> dias não precisam de tempo real e a regra mais urgente, a de umidade do solo, é atendida com varredura de
> poucos segundos. O sistema dá a triagem; a decisão de aplicar defensivo continua com o agrônomo.

---

### Números de referência (cenário padrão)

200 registros processados (117 laudos + 83 leituras), 2 rejeitados (JSON truncado e sensor marcando 140%),
12 alertas (R1 1 · R2 2 · R3 4 · R4 2 · R5 3), 24 ações (5 e-mails, 3 relatórios, 4 comandos de irrigação,
12 arquivos de alerta). Reenvio do dia 4: 0 novos, 31 já processados, nenhuma ação.
