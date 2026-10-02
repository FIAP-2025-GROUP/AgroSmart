"""Contrato analítico da Fase 2: campos, domínios e caminhos.

Este módulo é a fonte única da verdade sobre o formato que atravessa o
pipeline. O snapshot RAW, o schema explícito do Spark (Bronze/Silver), o pacote
processado e a API analítica leem daqui — mudar um campo é mudar a
`VERSAO_SCHEMA`.

Regra geral: **ausência permanece nula**. Um registro legado da Fase 1 não tem
`id_analise`, `id_imagem` nem localidade, e esses campos chegam vazios até o
dashboard, que os mostra como "não informado" em vez de inventar um valor.

Definições usadas em todo o projeto:

* **Análise** — uma execução do AgroSmart sobre uma imagem (`id_analise`).
* **Laudo** — um resultado emitido por um motor, CNN ou VLM (`id_laudo`). Uma
  análise no modo comparação tem dois laudos.
* **Imagem distinta** — conteúdo identificado por `id_imagem`. Reanalisar a
  mesma foto cria uma nova análise, não uma nova imagem distinta.
"""

from __future__ import annotations

from pathlib import Path

VERSAO_SCHEMA = "2.0.0"
VERSAO_NORMALIZACAO = "1.0.0"

RAIZ = Path(__file__).resolve().parent.parent.parent
PASTA_DADOS = RAIZ / "dados"
PASTA_SIMULADOS = PASTA_DADOS / "simulados"
PASTA_RAW = PASTA_DADOS / "raw"
PASTA_BRONZE = PASTA_DADOS / "bronze"
PASTA_SILVER = PASTA_DADOS / "silver"
PASTA_GOLD = PASTA_DADOS / "gold"
PASTA_PROCESSADOS = PASTA_DADOS / "processados"

ARQUIVO_SIMULADOS = PASTA_SIMULADOS / "laudos_simulados.jsonl"

# --------------------------------------------------------------------------- #
# Domínios controlados
# --------------------------------------------------------------------------- #
ORIGEM_REAL = "real"
ORIGEM_SIMULADO = "simulado"
ORIGENS = (ORIGEM_REAL, ORIGEM_SIMULADO)

# Códigos normalizados (sem acento) usados da Silver em diante. A Fase 1 grava
# "saudável", "doente" e "indeterminado"; a Silver converte.
CONDICAO_SAUDAVEL = "saudavel"
CONDICAO_DOENTE = "doente"
CONDICAO_INDETERMINADA = "indeterminado"
CONDICOES = (CONDICAO_SAUDAVEL, CONDICAO_DOENTE, CONDICAO_INDETERMINADA)

CATEGORIA_SAUDAVEL = "saudavel"
CATEGORIA_DOENCA = "doenca"
CATEGORIA_PRAGA = "praga"
CATEGORIA_OUTRA = "outra_anomalia"
CATEGORIA_INDETERMINADA = "indeterminado"
CATEGORIAS = (
    CATEGORIA_SAUDAVEL,
    CATEGORIA_DOENCA,
    CATEGORIA_PRAGA,
    CATEGORIA_OUTRA,
    CATEGORIA_INDETERMINADA,
)

MOTOR_CNN = "cnn"
MOTOR_VLM = "vlm"
MOTORES = (MOTOR_CNN, MOTOR_VLM)

TIPO_FOTO_CAMPO = "foto_campo"
TIPO_IMAGEM_REFERENCIA = "imagem_referencia"
TIPOS_AMOSTRA = (TIPO_FOTO_CAMPO, TIPO_IMAGEM_REFERENCIA)

# --------------------------------------------------------------------------- #
# Campos do RAW (um registro por laudo)
# --------------------------------------------------------------------------- #
# (nome, tipo). Os tipos usam o vocabulário do Spark (`string`, `double`) para
# que `transformacoes_spark` monte o StructType explícito a partir desta lista.
# `data_analise` viaja como texto ISO-8601 e só vira timestamp na Silver: assim
# uma data malformada não derruba a leitura, vira um rejeitado com motivo.
CAMPOS_LAUDO: list[tuple[str, str]] = [
    ("versao_schema", "string"),
    ("origem_instancia", "string"),
    ("id_analise", "string"),
    ("id_laudo", "string"),
    ("id_imagem", "string"),
    ("arquivo", "string"),
    ("data_analise", "string"),
    ("fuso_origem", "string"),
    ("uf", "string"),
    ("municipio", "string"),
    ("propriedade", "string"),
    ("talhao", "string"),
    ("modo_solicitado", "string"),
    ("limiar_cnn", "double"),
    ("origem_dado", "string"),
    ("tipo_amostra", "string"),
    ("especie_original", "string"),
    ("cultura", "string"),
    ("condicao", "string"),
    ("diagnostico_original", "string"),
    ("diagnostico_normalizado", "string"),
    ("categoria_problema", "string"),
    ("agente_original", "string"),
    ("motor", "string"),
    ("modelo_versao", "string"),
    ("confianca", "double"),
    ("confianca_texto", "string"),
    ("sintomas", "string"),
    ("manejo", "string"),
    ("observacoes", "string"),
]

# Acrescentados pelo snapshot RAW.
CAMPOS_SNAPSHOT: list[tuple[str, str]] = [
    ("id_snapshot", "string"),
    ("extraido_em", "string"),
]

CAMPOS_RAW = CAMPOS_LAUDO + CAMPOS_SNAPSHOT
NOMES_CAMPOS_RAW = [nome for nome, _ in CAMPOS_RAW]

# Acrescentados pelo pipeline (Bronze em diante).
CAMPOS_EXECUCAO = ["id_execucao", "processado_em", "versao_normalizacao"]

# Sem estes campos um laudo não pode ser interpretado; a Silver rejeita.
CAMPOS_OBRIGATORIOS = ("id_laudo", "data_analise", "origem_dado", "motor", "condicao")

# Campos cuja ausência é medida em `gold_qualidade`.
CAMPOS_MONITORADOS = (
    "id_analise",
    "id_imagem",
    "uf",
    "municipio",
    "propriedade",
    "talhao",
    "cultura",
    "modo_solicitado",
    "limiar_cnn",
    "tipo_amostra",
)

# --------------------------------------------------------------------------- #
# Pacote processado (saída do pipeline, entrada da API)
# --------------------------------------------------------------------------- #
ARQUIVOS_PACOTE = ("analises.jsonl", "laudos.jsonl", "agregados.jsonl", "qualidade.json")
MANIFESTO = "manifesto.json"
PONTEIRO_ATUAL = "ATUAL.json"
STATUS_COMPLETO = "completo"


def registro_vazio() -> dict:
    """Um laudo com todos os campos do contrato presentes e nulos."""
    return {nome: None for nome, _ in CAMPOS_LAUDO}
