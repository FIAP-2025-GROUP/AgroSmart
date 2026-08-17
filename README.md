# AgroSmart — Fase 1: Diagnóstico de Plantas por Visão Computacional

Recebe fotos de plantas, identifica a espécie, classifica como **saudável** ou
**doente**, aponta o agente causal, recomenda o manejo e exporta tudo em CSV e
JSON.

O sistema tem **dois motores de diagnóstico** com forças opostas — e a
comparação entre eles é um resultado do trabalho, não um detalhe de
implementação.

| | CNN especializado | Visão generalista (VLM) |
|---|---|---|
| Modelo | MobileNetV2 treinado por nós | Google Gemini |
| Cobertura | 12 condições em 5 culturas | Qualquer planta |
| Confiança | Probabilidade real (softmax) | Autoavaliação em palavras |
| Acurácia | Mensurável por classe | Não mensurável por classe |
| Custo | Zero, roda offline | Chamada de API (camada gratuita) |
| Latência | ~100 ms por imagem | ~2 s por imagem |

**Modo automático:** o CNN responde primeiro; se nenhuma classe atingir a
confiança mínima, a imagem passa para o generalista. Isso dá diagnóstico
medido onde o modelo treinado é competente e cobertura aberta no resto.

---

## Requisitos

- Python 3.12
- ~800 MB livres (TensorFlow)
- Chave gratuita do Gemini (opcional — só para o motor generalista)

No Windows o comando `python` costuma cair na stub da Microsoft Store. Use o
launcher `py`:

```bash
py -3.12 -m venv .venv
```

Instale as dependências:

```bash
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

### Chave do Gemini

Pegue uma chave gratuita em <https://aistudio.google.com/apikey> — não pede
cartão. Depois copie o modelo e preencha:

```bash
copy .env.example .env
```

Abra o `.env` e substitua o placeholder pela sua chave:

```
GEMINI_API_KEY=AIza...
```

Pronto — app e linha de comando leem daí sozinhos, sem digitar nada de novo.

Ordem de precedência: variável de ambiente do sistema → `.env` → campo da barra
lateral do app. A barra lateral serve para uso pontual; a chave digitada ali
vale só naquela sessão do navegador.

Sem chave nenhuma, o motor CNN continua funcionando normalmente.

> **O `.env` está no `.gitignore` e não pode ir no `.zip` da entrega.** Antes de
> compactar, confira que ele não está lá — a chave é pessoal e fica exposta a
> quem receber o arquivo. O `.env.example`, que não tem valor real, pode e deve
> ser versionado.

---

## Como rodar

**Interface web:**

```bash
.venv\Scripts\python.exe servidor.py
```

Abra <http://localhost:8000>. Arraste uma foto para o centro da tela (ou clique,
ou cole com `Ctrl+V`) e o laudo aparece abaixo. Cada análise é salva no
histórico automaticamente, com a miniatura da foto — clique num cartão para
reabrir o laudo depois.

Para abrir do celular, na mesma rede Wi-Fi:

```bash
.venv\Scripts\python.exe servidor.py --publico
```

**Linha de comando (lote de uma pasta):**

```bash
.venv\Scripts\python.exe classificar_pasta.py dados/campo --motor comparacao
```

O `--motor` aceita `auto`, `cnn`, `vlm` e `comparacao`. O modo `comparacao`
roda os dois motores em cada imagem e grava as duas linhas no CSV — é o que
alimenta a tabela comparativa do relatório.

---

## Treinar o modelo CNN

O treino roda no **Google Colab** com GPU gratuita — não precisa de nada
instalado na máquina.

1. Abra `notebooks/treino_agrosmart.ipynb` no Colab.
2. `Ambiente de execução` → `Alterar o tipo` → **GPU T4**.
3. `Executar tudo` (cerca de 20–30 minutos).
4. Baixe o `agrosmart_modelo.zip` gerado e distribua o conteúdo:
   - `agrosmart_mobilenetv2.keras` e `classes.json` → `modelo/`
   - `*.png` e `metricas.json` → `docs/`

### Desenvolver sem o modelo treinado

```bash
.venv\Scripts\python.exe ferramentas\gerar_stub.py
```

Gera um modelo de mesma arquitetura com o topo aleatório. Os diagnósticos do
CNN serão aleatórios — serve só para validar o fluxo.

---

## Classes do CNN especializado

| Cultura | Condições |
|---|---|
| Maçã | Saudável · Sarna-da-macieira |
| Milho | Saudável · Ferrugem comum |
| Uva | Saudável · Podridão-negra |
| Batata | Saudável · Pinta-preta |
| Tomate | Saudável · Requeima · Mancha de septória · **Ácaro-rajado** (praga) |

O rótulo binário exigido pela atividade (*saudável* / *doente*) é derivado da
classe prevista. Fora desta lista — roseiras, ornamentais, frutíferas não
treinadas — só o motor generalista consegue diagnosticar.

### Rejeição de imagens fora do domínio

Quando nenhuma classe atinge a confiança mínima (padrão **60%**), o CNN se
declara **indeterminado** em vez de emitir um laudo errado com aparência de
certeza. No modo automático é esse limiar que aciona o generalista. Ajustável
pela barra lateral.

---

## Formato da exportação

17 campos por laudo, entre eles `motor`, `especie`, `diagnostico`, `condicao`,
`sintomas_observados`, `manejo_recomendado` e `observacoes`.

- **CSV** — separador `;`, decimal `,` e encoding `utf-8-sig`. É o dialeto que o
  Excel em português abre com colunas separadas e acentos corretos.
- **JSON** — bloco `metadata` (contagens por motor e por condição, limiar, data)
  e lista `resultados`.

A coluna `confianca` carrega a probabilidade do CNN e **fica vazia nas linhas do
generalista**: converter uma autoavaliação de "média" em um número daria a ela
uma precisão que não tem. A confiança legível dos dois motores está em
`confianca_texto`.

---

## Estrutura

```
servidor.py               API FastAPI + serve o frontend
web/                      Interface (index.html, estilo.css, app.js)
classificar_pasta.py      Diagnóstico em lote por linha de comando
app.py                    Interface Streamlit antiga (mantida como reserva)
.env.example              Modelo de configuração (copie para .env)
src/
  config.py               Carrega o .env e resolve a chave do Gemini
  historico.py            Persistência dos laudos em SQLite
  tipos.py                Resultado compartilhado pelos dois motores
  rotulos.py              Catálogo das 12 classes do CNN
  arquitetura.py          Definição da rede
  inferencia.py           Motor CNN
  vlm.py                  Motor generalista (Gemini)
  diagnostico.py          Orquestração entre os dois motores
  exportacao.py           Geração do CSV e do JSON
ferramentas/gerar_stub.py Modelo de pesos aleatórios para desenvolvimento
notebooks/                Notebook de treino (Colab)
modelo/                   Modelo treinado + classes.json
dados/exemplos/           Amostras do PlantVillage
dados/campo/              Fotos reais de celular
docs/                     Gráficos e métricas gerados no treino
exportacoes/              Saída do CSV e do JSON
```

`src/rotulos.py` define a ordem dos índices de saída da rede. O app compara essa
ordem com o `classes.json` do modelo ao carregar e recusa arquivos incompatíveis,
em vez de produzir diagnósticos trocados silenciosamente.

---

## Limitações conhecidas

**CNN.** O PlantVillage é composto por folhas destacadas sobre fundo uniforme de
laboratório. A acurácia medida no conjunto de teste **não** se transfere
integralmente para fotos de celular no campo, com sombra, solo ao fundo e foco
irregular. Por isso o projeto mantém `dados/exemplos/` (padrão PlantVillage) e
`dados/campo/` (fotos reais) separados — a comparação entre as duas acurácias
está no relatório técnico.

**Generalista.** Não tem acurácia mensurável por classe: a "confiança" é uma
autoavaliação do próprio modelo, não uma probabilidade calibrada. Pode errar a
espécie e pode descrever sintomas plausíveis que não estão na foto. O laudo é
uma **triagem**, não um parecer técnico — decisões de aplicação de defensivo
exigem engenheiro agrônomo.
