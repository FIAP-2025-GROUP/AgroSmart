"""Transformações Bronze → Silver → Gold com PySpark DataFrames.

Usado de dois lugares, com o mesmo código:

* `ferramentas/executar_pipeline.py` — Spark local (`local[*]`);
* `notebooks/pipeline_fase2_databricks.ipynb` — sessão `spark` do Databricks.

Decisões que valem para os dois:

* Só a API de DataFrame. Nada de RDD, `SparkContext` ou UDF Python: as regras
  de normalização viram expressões `CASE WHEN` geradas a partir das tabelas de
  `normalizacao.py`, que o Spark executa sem serializar linha por linha para o
  Python.
* Funções `try_*` em vez de cast/parse diretos. Com ANSI mode ligado (padrão nos
  runtimes Spark 4 do Databricks) um `to_timestamp` sobre texto inválido aborta
  o job; `try_to_timestamp` devolve nulo, e o registro vira um rejeitado com
  motivo. Um laudo ruim nunca derruba o lote.
* Datas sem fuso (o histórico da Fase 1 grava hora local sem offset) são lidas
  no fuso da sessão. Configure `spark.sql.session.timeZone` como
  `America/Sao_Paulo` — `criar_sessao_local` já faz isso.

Este módulo não carrega TensorFlow, o modelo Keras nem o Gemini: o Spark só
processa dados tabulares.
"""

from __future__ import annotations

from datetime import datetime, timezone

from pyspark.sql import Column, DataFrame, SparkSession, Window
from pyspark.sql import functions as F
from pyspark.sql import types as T

from . import contratos as c
from . import normalizacao as n

FUSO_SESSAO = "America/Sao_Paulo"
COLUNA_CORROMPIDA = "_registro_corrompido"

_TIPOS = {"string": T.StringType(), "double": T.DoubleType()}

# Formatos aceitos para `data_analise`, testados em ordem.
FORMATOS_DATA = (
    "yyyy-MM-dd'T'HH:mm:ssXXX",
    "yyyy-MM-dd'T'HH:mm:ss.SSSSSSXXX",
    "yyyy-MM-dd'T'HH:mm:ss",
    "yyyy-MM-dd'T'HH:mm:ss.SSSSSS",
    "yyyy-MM-dd HH:mm:ss",
)

OPCOES_JSON = {"ignoreNullFields": "false", "timestampFormat": "yyyy-MM-dd'T'HH:mm:ssXXX",
               "dateFormat": "yyyy-MM-dd"}


# --------------------------------------------------------------------------- #
# Sessão e leitura
# --------------------------------------------------------------------------- #
def criar_sessao_local(nome: str = "agrosmart-fase2") -> SparkSession:
    """SparkSession local para desenvolvimento. No Databricks use `spark`."""
    return (
        SparkSession.builder.master("local[*]")
        .appName(nome)
        .config("spark.sql.session.timeZone", FUSO_SESSAO)
        .config("spark.sql.shuffle.partitions", "4")
        .config("spark.ui.enabled", "false")
        .config("spark.sql.jsonGenerator.ignoreNullFields", "false")
        .getOrCreate()
    )


def schema_raw() -> T.StructType:
    """Schema explícito do RAW, montado a partir do contrato.

    A coluna extra recebe a linha inteira quando ela não é JSON válido ou traz
    um tipo incompatível (texto onde o contrato pede número).
    """
    campos = [T.StructField(nome, _TIPOS[tipo], True) for nome, tipo in c.CAMPOS_RAW]
    campos.append(T.StructField(COLUNA_CORROMPIDA, T.StringType(), True))
    return T.StructType(campos)


def ler_raw(spark: SparkSession, caminho: str) -> DataFrame:
    """Lê `laudos.jsonl` (arquivo ou pasta) com schema explícito e modo permissivo."""
    return (
        spark.read.schema(schema_raw())
        .option("mode", "PERMISSIVE")
        .option("columnNameOfCorruptRecord", COLUNA_CORROMPIDA)
        .json(str(caminho))
    )


def agora_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


# --------------------------------------------------------------------------- #
# Bronze
# --------------------------------------------------------------------------- #
def bronze(df_raw: DataFrame, id_execucao: str, processado_em: str, persistir: bool = True) -> DataFrame:
    """Registros exatamente como chegaram, mais a procedência da execução.

    Nada é descartado nem corrigido aqui — inclusive as linhas corrompidas, que
    seguem com o texto original em `_registro_corrompido`. O `cache` também
    contorna uma restrição do Spark: consultas que só tocam a coluna de
    registro corrompido não são permitidas direto sobre o arquivo JSON.

    `persistir=False` no Databricks serverless, que não aceita `cache()`: lá a
    Bronze é gravada como tabela Delta e relida, o que tem o mesmo efeito.
    """
    df = (
        df_raw.withColumn("id_execucao", F.lit(id_execucao))
        .withColumn("processado_em", F.lit(processado_em))
        .withColumn("versao_normalizacao", F.lit(c.VERSAO_NORMALIZACAO))
    )
    return df.cache() if persistir else df


# --------------------------------------------------------------------------- #
# Expressões de normalização (equivalentes às funções de `normalizacao.py`)
# --------------------------------------------------------------------------- #
def _limpo(coluna: Column) -> Column:
    texto = F.trim(F.regexp_replace(coluna, r"\s+", " "))
    return F.when(F.lower(texto).isin(*n.VAZIOS), F.lit(None)).otherwise(texto)


def _chave(coluna: Column) -> Column:
    texto = F.translate(F.lower(coluna), n.ACENTOS, n.SEM_ACENTOS)
    texto = F.trim(F.regexp_replace(texto, "[^a-z0-9]+", " "))
    return F.when(texto == "", F.lit(None)).otherwise(texto)


def _contem_palavra(chave_col: Column, termo: str) -> Column:
    return F.concat(F.lit(" "), chave_col, F.lit(" ")).contains(f" {termo} ")


def _sinonimo(chave_col: Column, tabela: dict) -> Column:
    """CASE WHEN que devolve o sinônimo mais longo contido na chave (ou nulo).

    A ordem (comprimento, texto) decrescente reproduz o `max` de
    `normalizacao._melhor_sinonimo`. Quem chama grava o sinônimo numa coluna e
    só depois o traduz com `_mapear` — repetir este CASE grande em várias
    expressões estoura o limite de 64 KB do código gerado pelo Spark.
    """
    padded = F.concat(F.lit(" "), chave_col, F.lit(" "))
    expressao = None
    for sinonimo in sorted(tabela, key=lambda s: (len(s), s), reverse=True):
        condicao = padded.contains(f" {sinonimo} ")
        expressao = (F.when(condicao, F.lit(sinonimo)) if expressao is None
                     else expressao.when(condicao, F.lit(sinonimo)))
    return expressao


def _mapear(coluna: Column, mapa: dict) -> Column:
    expressao = None
    for chave_mapa, valor in mapa.items():
        expressao = (F.when(coluna == chave_mapa, F.lit(valor)) if expressao is None
                     else expressao.when(coluna == chave_mapa, F.lit(valor)))
    return expressao


def _motor(coluna: Column) -> Column:
    k = _chave(coluna)
    expressao = None
    for fragmento, motor in n.FRAGMENTOS_MOTOR:
        cond = _contem_palavra(k, fragmento)
        expressao = F.when(cond, F.lit(motor)) if expressao is None else expressao.when(cond, F.lit(motor))
    return expressao


def _categoria_por_palavras(*colunas: Column) -> Column:
    alvo = F.concat_ws(" ", *[_chave(col) for col in colunas])
    expressao = None
    for categoria, padrao in n.PADROES_CATEGORIA:
        cond = alvo.rlike(padrao)
        expressao = F.when(cond, F.lit(categoria)) if expressao is None else expressao.when(cond, F.lit(categoria))
    return expressao.otherwise(F.lit(c.CATEGORIA_OUTRA))


# --------------------------------------------------------------------------- #
# Silver
# --------------------------------------------------------------------------- #
def _normalizar(df: DataFrame) -> DataFrame:
    strings = [nome for nome, tipo in c.CAMPOS_RAW if tipo == "string"]
    for nome in strings:
        df = df.withColumn(nome, _limpo(F.col(nome)))

    data = F.coalesce(*[F.try_to_timestamp(F.col("data_analise"), F.lit(f)) for f in FORMATOS_DATA])
    df = (
        df.withColumn("condicao_original", F.col("condicao"))
        .withColumn("motor_original", F.col("motor"))
        .withColumn("data_analise_ts", data)
        .withColumn("condicao", _mapear(_chave(F.col("condicao")), n.CONDICAO_POR_CHAVE))
        .withColumn("motor", _motor(F.col("motor")))
        .withColumn("origem_dado", F.when(_chave(F.col("origem_dado")).isin(*c.ORIGENS),
                                          _chave(F.col("origem_dado"))))
        .withColumn("tipo_amostra", F.when(F.col("tipo_amostra").isin(*c.TIPOS_AMOSTRA), F.col("tipo_amostra")))
    )

    # Chaves e sinônimos ficam em colunas próprias: cada CASE grande roda uma vez.
    df = (
        df.withColumn("_sin_cultura", _sinonimo(_chave(F.col("cultura")), n.SINONIMOS_CULTURA))
        .withColumn("_sin_especie", _sinonimo(_chave(F.col("especie_original")), n.SINONIMOS_CULTURA))
        .withColumn("_sin_diag", _sinonimo(_chave(F.col("diagnostico_original")), n.SINONIMOS_DIAGNOSTICO))
    )

    # Cultura: sinônimo em `cultura`, senão em `especie_original`, senão o texto limpo.
    sinonimo_cultura = F.coalesce(F.col("_sin_cultura"), F.col("_sin_especie"))
    df = (
        df.withColumn("cultura_mapeada", sinonimo_cultura.isNotNull())
        .withColumn("cultura", F.coalesce(_mapear(sinonimo_cultura, n.SINONIMOS_CULTURA),
                                          F.col("cultura"), F.col("especie_original")))
    )

    # Diagnóstico: o original fica intacto; o normalizado sai do catálogo de sinônimos.
    sin_diag = F.col("_sin_diag")
    diag_sin = _mapear(sin_diag, {k: v[0] for k, v in n.SINONIMOS_DIAGNOSTICO.items()})
    cat_sin = _mapear(sin_diag, {k: v[1] for k, v in n.SINONIMOS_DIAGNOSTICO.items()})
    condicao = F.col("condicao")
    return (
        df.withColumn(
            "diagnostico_normalizado",
            F.when(condicao == c.CONDICAO_SAUDAVEL, F.lit(n.DIAGNOSTICO_SAUDAVEL))
            .when(condicao == c.CONDICAO_INDETERMINADA, F.lit(n.DIAGNOSTICO_INDETERMINADO))
            .when(condicao == c.CONDICAO_DOENTE, F.coalesce(diag_sin, F.col("diagnostico_original"))),
        )
        .withColumn(
            "categoria_problema",
            F.when(condicao == c.CONDICAO_SAUDAVEL, F.lit(c.CATEGORIA_SAUDAVEL))
            .when(condicao == c.CONDICAO_INDETERMINADA, F.lit(c.CATEGORIA_INDETERMINADA))
            .when(condicao == c.CONDICAO_DOENTE,
                  F.coalesce(cat_sin, _categoria_por_palavras(F.col("diagnostico_original"),
                                                              F.col("agente_original")))),
        )
        .withColumn("diagnostico_mapeado",
                    F.when(condicao == c.CONDICAO_DOENTE, sin_diag.isNotNull())
                    .otherwise(condicao.isNotNull()))
        .drop("_sin_cultura", "_sin_especie", "_sin_diag")
    )


def _motivos() -> Column:
    """Lista de motivos de rejeição; vazia quando o registro é válido."""
    corrompido = F.col(COLUNA_CORROMPIDA).isNotNull()
    regras = [
        ("json_invalido_ou_tipo_incompativel", corrompido),
        ("versao_schema_incompativel",
         ~corrompido & F.col("versao_schema").isNotNull() & ~F.col("versao_schema").startswith("2.")),
        ("id_laudo_ausente", ~corrompido & F.col("id_laudo").isNull()),
        ("data_ausente", ~corrompido & F.col("data_analise").isNull()),
        ("data_invalida", ~corrompido & F.col("data_analise").isNotNull() & F.col("data_analise_ts").isNull()),
        ("origem_dado_invalida", ~corrompido & F.col("origem_dado").isNull()),
        ("motor_desconhecido", ~corrompido & F.col("motor").isNull()),
        ("condicao_invalida", ~corrompido & F.col("condicao").isNull()),
        ("confianca_fora_do_intervalo",
         F.col("confianca").isNotNull() & ((F.col("confianca") < 0) | (F.col("confianca") > 1))),
        # A autoavaliação do Gemini não é probabilidade: número ali é violação do contrato.
        ("confianca_numerica_em_laudo_vlm", (F.col("motor") == c.MOTOR_VLM) & F.col("confianca").isNotNull()),
        ("limiar_invalido",
         F.col("limiar_cnn").isNotNull() & ((F.col("limiar_cnn") <= 0) | (F.col("limiar_cnn") > 1))),
    ]
    return F.filter(F.array(*[F.when(cond, F.lit(motivo)) for motivo, cond in regras]),
                    lambda m: m.isNotNull())


COLUNAS_SILVER = (
    [nome for nome, _ in c.CAMPOS_LAUDO]
    + ["condicao_original", "motor_original", "cultura_mapeada", "diagnostico_mapeado",
       "data_analise_ts", "data_dia", "semana"]
    + [nome for nome, _ in c.CAMPOS_SNAPSHOT]
    + c.CAMPOS_EXECUCAO
)


# Identidade é composta: o mesmo `id_laudo`/`id_analise` em outra instalação ou
# em outra origem (real × simulado) é OUTRO registro, nunca uma duplicata.
CHAVE_LAUDO = ("origem_dado", "origem_instancia", "id_laudo")
CHAVE_ANALISE = ("origem_dado", "origem_instancia", "id_analise")

# Campos que descrevem a análise (e não o laudo): todos os laudos de uma mesma
# análise precisam concordar neles.
CAMPOS_CONTEXTO_ANALISE = ("id_imagem", "arquivo", "uf", "municipio", "propriedade", "talhao",
                           "modo_solicitado", "limiar_cnn", "tipo_amostra")


def _particao(chave: tuple[str, ...]) -> list[Column]:
    # Nulo vira texto vazio só para particionar; o valor gravado continua nulo.
    return [F.coalesce(F.col(nome).cast("string"), F.lit("")) for nome in chave]


def _hash_campos(campos) -> Column:
    return F.sha2(F.to_json(F.struct(*[F.col(nome) for nome in campos]), OPCOES_JSON), 256)


def silver(df_bronze: DataFrame) -> tuple[DataFrame, DataFrame]:
    """(laudos válidos, rejeitados com motivo).

    Ordem: normaliza → valida → resolve identidade. A identidade de um laudo é
    `origem_dado + origem_instancia + id_laudo`. Entre os registros válidos com a
    mesma identidade:

    * todos com o **mesmo conteúdo** (campos do contrato, sem os de procedência
      do snapshot) → fica um; a regra é determinística (extração mais recente,
      depois `id_snapshot`), e as cópias vão para os rejeitados como `duplicado`;
    * **conteúdos diferentes** → nenhum é escolhido: todos vão para os
      rejeitados como `conflito_identidade_laudo`, para a qualidade mostrar.
    """
    df = _normalizar(df_bronze).withColumn("_motivos", _motivos())
    valido = F.size("_motivos") == 0
    df = df.withColumn("_conteudo", _hash_campos([nome for nome, _ in c.CAMPOS_LAUDO]))

    grupo = Window.partitionBy(*_particao(CHAVE_LAUDO))
    conteudos = F.size(F.collect_set(F.when(valido, F.col("_conteudo"))).over(grupo))
    # Só os válidos disputam a vaga: um inválido mais novo não "vence" o válido.
    ordem = Window.partitionBy(*_particao(CHAVE_LAUDO)).orderBy(
        (~valido).asc(), F.col("extraido_em").desc_nulls_last(),
        F.col("id_snapshot").desc_nulls_last(), F.col("_conteudo").asc())
    df = df.withColumn(
        "_motivos",
        F.when(valido & (conteudos > 1), F.array(F.lit("conflito_identidade_laudo")))
        .when(valido & (F.row_number().over(ordem) > 1), F.array(F.lit("duplicado")))
        .otherwise(F.col("_motivos")),
    )

    validos = (
        df.filter(F.size("_motivos") == 0)
        .withColumn("data_dia", F.to_date("data_analise_ts"))
        .withColumn("semana", F.to_date(F.date_trunc("week", "data_analise_ts")))
        .select(*COLUNAS_SILVER)
    )

    campos_originais = [F.col(nome) for nome, _ in c.CAMPOS_RAW]
    rejeitados = df.filter(F.size("_motivos") > 0).select(
        "id_laudo", "id_analise", "origem_dado", "origem_instancia", "id_snapshot", "id_execucao", "processado_em",
        F.col("_motivos").alias("motivos"),
        F.concat_ws(";", "_motivos").alias("motivo_rejeicao"),
        F.coalesce(F.col(COLUNA_CORROMPIDA),
                   F.to_json(F.struct(*campos_originais), OPCOES_JSON)).alias("registro_original"),
    )
    return validos, rejeitados


# --------------------------------------------------------------------------- #
# Gold
# --------------------------------------------------------------------------- #
def gold_laudos(df_silver: DataFrame) -> DataFrame:
    """Todos os laudos válidos — inclusive os legados sem `id_analise`."""
    return df_silver


def _com_contexto(df_silver: DataFrame) -> DataFrame:
    """Laudos com análise, a chave composta da análise e quantos contextos
    distintos essa análise tem entre os seus laudos (1 = coerente)."""
    chave = F.concat_ws("|", *_particao(CHAVE_ANALISE))
    return (
        df_silver.filter(F.col("id_analise").isNotNull())
        .withColumn("_chave_analise", chave)
        .withColumn("_contexto", _hash_campos(CAMPOS_CONTEXTO_ANALISE))
        .withColumn("_contextos", F.size(F.collect_set("_contexto").over(Window.partitionBy("_chave_analise"))))
    )


def gold_analises(df_silver: DataFrame) -> DataFrame:
    """Uma linha por análise identificada: a unidade dos indicadores principais.

    Laudos sem `id_analise` (histórico legado) ficam de fora — agrupá-los por
    arquivo ou horário seria inventar análises.

    Quando a análise tem mais de um laudo (modo comparação), o **laudo
    principal** define condição, diagnóstico e categoria, pela mesma lógica do
    modo automático da Fase 1: vale o CNN se ele foi conclusivo; senão o VLM se
    ele foi conclusivo; senão a análise é indeterminada. Os dois veredictos
    continuam disponíveis em `condicao_cnn`/`condicao_vlm`.
    """
    laudos = _com_contexto(df_silver)
    # Análise cujos laudos discordam no contexto (imagem, local, modo...) não é
    # montada: escolher um dos lados seria inventar. Ela é contada na qualidade
    # como `analise_contexto_conflitante`; os laudos seguem em `gold_laudos`.
    laudos = laudos.filter(F.col("_contextos") == 1)
    motor, condicao = F.col("motor"), F.col("condicao")
    prioridade = (
        F.when((motor == c.MOTOR_CNN) & (condicao != c.CONDICAO_INDETERMINADA), 0)
        .when((motor == c.MOTOR_VLM) & (condicao != c.CONDICAO_INDETERMINADA), 1)
        .when(motor == c.MOTOR_CNN, 2)
        .otherwise(3)
    )
    ordem = Window.partitionBy("_chave_analise").orderBy(prioridade, "data_analise_ts", "id_laudo")
    principal = (
        laudos.withColumn("_ordem", F.row_number().over(ordem))
        .filter(F.col("_ordem") == 1)
        .select(
            "_chave_analise", "id_analise", "origem_dado", "origem_instancia", "id_imagem", "arquivo",
            "fuso_origem",
            "uf", "municipio", "propriedade", "talhao", "modo_solicitado", "limiar_cnn", "tipo_amostra",
            F.col("cultura").alias("_cultura_principal"), "condicao", "diagnostico_normalizado",
            "categoria_problema", F.col("motor").alias("motor_principal"),
            F.col("id_laudo").alias("id_laudo_principal"), "id_snapshot", "id_execucao",
            "processado_em", "versao_normalizacao",
        )
    )

    resumo = laudos.groupBy("_chave_analise").agg(
        F.count("*").alias("n_laudos"),
        F.min("data_analise_ts").alias("data_analise_ts"),
        F.max((motor == c.MOTOR_CNN).cast("int")).alias("_tem_cnn"),
        F.max((motor == c.MOTOR_VLM).cast("int")).alias("_tem_vlm"),
        F.min(F.when(motor == c.MOTOR_CNN, condicao)).alias("condicao_cnn"),
        F.min(F.when(motor == c.MOTOR_VLM, condicao)).alias("condicao_vlm"),
        F.max(F.when(motor == c.MOTOR_CNN, F.col("confianca"))).alias("confianca_cnn"),
        F.min("cultura").alias("_cultura_qualquer"),
    )

    concordam = F.when(
        F.col("condicao_cnn").isNotNull() & F.col("condicao_vlm").isNotNull()
        & (F.col("condicao_cnn") != c.CONDICAO_INDETERMINADA)
        & (F.col("condicao_vlm") != c.CONDICAO_INDETERMINADA),
        F.col("condicao_cnn") == F.col("condicao_vlm"),
    )
    analises = (
        principal.join(resumo, "_chave_analise")
        .withColumn("cultura", F.coalesce("_cultura_principal", "_cultura_qualquer"))
        .withColumn("tem_cnn", F.col("_tem_cnn") == 1)
        .withColumn("tem_vlm", F.col("_tem_vlm") == 1)
        .withColumn("comparacao", F.col("tem_cnn") & F.col("tem_vlm"))
        .withColumn("concordancia_motores", concordam)
        .withColumn("data_dia", F.to_date("data_analise_ts"))
        .withColumn("semana", F.to_date(F.date_trunc("week", "data_analise_ts")))
    )

    # Reanálise: a mesma imagem (`id_imagem`) já tinha sido analisada antes.
    # Sem `id_imagem` não há como saber — o campo fica nulo, não falso.
    # A imagem é identificada por origem + id_imagem: real e simulado nunca se cruzam.
    por_imagem = Window.partitionBy(*_particao(("origem_dado", "id_imagem"))).orderBy(
        "data_analise_ts", "origem_instancia", "id_analise")
    analises = analises.withColumn(
        "e_reanalise",
        F.when(F.col("id_imagem").isNotNull(), F.row_number().over(por_imagem) > 1),
    )

    return analises.select(
        "id_analise", "origem_dado", "origem_instancia", "id_imagem", "arquivo",
        "data_analise_ts", "data_dia", "semana", "fuso_origem",
        "uf", "municipio", "propriedade", "talhao",
        "modo_solicitado", "limiar_cnn", "tipo_amostra",
        "cultura", "condicao", "diagnostico_normalizado", "categoria_problema",
        "motor_principal", "id_laudo_principal", "n_laudos", "tem_cnn", "tem_vlm", "comparacao",
        "condicao_cnn", "condicao_vlm", "concordancia_motores", "confianca_cnn", "e_reanalise",
        "id_snapshot", "id_execucao", "processado_em", "versao_normalizacao",
    )


DIMENSOES_AGREGADO = ("semana", "origem_dado", "uf", "municipio", "cultura")


def gold_agregados(df_analises: DataFrame) -> DataFrame:
    """Contagens semanais por origem, localidade e cultura.

    Cada linha leva o **denominador** (`n_analises`) e os **numeradores**. Para
    reagrupar (por mês, por UF...), some numeradores e denominadores e só então
    divida — nunca tire média dos percentuais, que dá peso igual a uma semana
    com 3 análises e a outra com 300.
    """
    def contar(coluna: str, valor: str) -> Column:
        return F.sum(F.when(F.col(coluna) == valor, 1).otherwise(0)).alias(f"n_{valor}")

    agregados = df_analises.groupBy(*DIMENSOES_AGREGADO).agg(
        F.count("*").alias("n_analises"),
        *[contar("condicao", v) for v in c.CONDICOES],
        *[contar("categoria_problema", v) for v in (c.CATEGORIA_DOENCA, c.CATEGORIA_PRAGA, c.CATEGORIA_OUTRA)],
    )
    for condicao in c.CONDICOES:
        agregados = agregados.withColumn(
            f"pct_{condicao}", F.round(F.col(f"n_{condicao}") / F.col("n_analises"), 4))
    return agregados


def gold_qualidade(df_bronze: DataFrame, df_silver: DataFrame, df_rejeitados: DataFrame,
                   df_analises: DataFrame) -> DataFrame:
    """Métricas de qualidade em formato longo: (origem, métrica, valor, denominador).

    Formato longo para caber igual numa tabela Delta e num JSON, e para cada
    número carregar o próprio denominador.
    """
    def origem() -> Column:
        return F.coalesce(F.col("origem_dado"), F.lit("desconhecida")).alias("origem_dado")

    def linhas(df: DataFrame, metricas: dict[str, Column], denominador: str) -> DataFrame:
        agregado = df.groupBy(origem()).agg(
            F.count("*").alias("_den"),
            *[F.sum(cond.cast("int")).alias(nome) for nome, cond in metricas.items()],
        )
        return (
            agregado.unpivot(["origem_dado", "_den"], list(metricas), "metrica", "valor")
            .withColumn("denominador", F.col("_den"))
            .withColumn("unidade", F.lit(denominador))
            .select("origem_dado", "metrica", "valor", "denominador", "unidade")
        )

    # No Bronze a origem ainda é o texto recebido; normaliza do mesmo jeito da
    # Silver para que os denominadores casem com os rejeitados.
    chave_origem = _chave(_limpo(F.col("origem_dado")))
    total_raw = df_bronze.withColumn(
        "origem_dado", F.when(chave_origem.isin(*c.ORIGENS), chave_origem)
    ).groupBy(origem()).agg(F.count("*").alias("valor")).select(
        "origem_dado", F.lit("registros_recebidos").alias("metrica"), "valor",
        F.col("valor").alias("denominador"), F.lit("registro_raw").alias("unidade"))

    rejeitados = (
        df_rejeitados.withColumn("motivo", F.explode("motivos"))
        .groupBy(origem(), "motivo").agg(F.count("*").alias("valor"))
        .join(total_raw.select("origem_dado", F.col("valor").alias("denominador")), "origem_dado")
        .select("origem_dado", F.concat(F.lit("rejeitado:"), "motivo").alias("metrica"),
                "valor", "denominador", F.lit("registro_raw").alias("unidade"))
    )
    rejeitados_total = (
        df_rejeitados.groupBy(origem()).agg(F.count("*").alias("valor"))
        .join(total_raw.select("origem_dado", F.col("valor").alias("denominador")), "origem_dado")
        .select("origem_dado", F.lit("rejeitados").alias("metrica"), "valor", "denominador",
                F.lit("registro_raw").alias("unidade"))
    )

    laudos = linhas(df_silver, {
        "laudos_validos": F.lit(True),
        **{f"ausente:{campo}": F.col(campo).isNull() for campo in c.CAMPOS_MONITORADOS},
        "localidade_ausente": F.col("uf").isNull() | F.col("municipio").isNull(),
        "identidade_incompleta": F.col("id_analise").isNull() | F.col("id_imagem").isNull(),
        "laudo_sem_analise": F.col("id_analise").isNull(),
        "diagnostico_nao_mapeado": (F.col("condicao") == c.CONDICAO_DOENTE) & ~F.col("diagnostico_mapeado"),
    }, "laudo")

    analises = linhas(df_analises, {
        "analises_identificadas": F.lit(True),
        "analise_localidade_ausente": F.col("uf").isNull() | F.col("municipio").isNull(),
        "analise_sem_id_imagem": F.col("id_imagem").isNull(),
        "reanalises": F.coalesce(F.col("e_reanalise"), F.lit(False)),
        "comparacoes": F.col("comparacao"),
        "comparacoes_discordantes": F.coalesce(~F.col("concordancia_motores"), F.lit(False)),
    }, "analise")

    por_analise = _com_contexto(df_silver).groupBy("_chave_analise").agg(
        F.first("origem_dado").alias("origem_dado"), F.max("_contextos").alias("_contextos"))
    conflitos = linhas(por_analise, {
        "analise_contexto_conflitante": F.col("_contextos") > 1,
    }, "analise_declarada")

    return total_raw.unionByName(rejeitados_total).unionByName(rejeitados) \
        .unionByName(laudos).unionByName(analises).unionByName(conflitos) \
        .withColumn("valor", F.col("valor").cast("long")) \
        .withColumn("denominador", F.col("denominador").cast("long"))


# --------------------------------------------------------------------------- #
# Orquestração
# --------------------------------------------------------------------------- #
def executar(spark: SparkSession, caminho_raw: str, id_execucao: str,
             processado_em: str | None = None) -> dict[str, DataFrame]:
    """Roda o pipeline inteiro e devolve todas as camadas como DataFrames.

    Nada é gravado aqui: quem chama decide o destino (arquivos locais, tabelas
    Delta no Databricks, ou só inspeção nos testes).
    """
    processado_em = processado_em or agora_utc()
    df_bronze = bronze(ler_raw(spark, caminho_raw), id_execucao, processado_em)
    df_silver, df_rejeitados = silver(df_bronze)
    df_silver = df_silver.cache()
    df_rejeitados = df_rejeitados.cache()
    df_analises = gold_analises(df_silver).cache()
    return {
        "bronze": df_bronze,
        "silver": df_silver,
        "silver_rejeitados": df_rejeitados,
        "gold_analises": df_analises,
        "gold_laudos": gold_laudos(df_silver),
        "gold_agregados": gold_agregados(df_analises),
        "gold_qualidade": gold_qualidade(df_bronze, df_silver, df_rejeitados, df_analises),
    }


def para_linhas_json(df: DataFrame, ordem: list[str] | None = None) -> list[str]:
    """DataFrame -> linhas JSON (nulos explícitos, datas ISO) no driver.

    Usado para publicar o pacote processado, que é pequeno (milhares de linhas).
    """
    if ordem:
        df = df.orderBy(*ordem)
    return [linha[0] for linha in df.select(F.to_json(F.struct(*df.columns), OPCOES_JSON)).collect()]


def exportar_pacote(
    df_analises: DataFrame,
    df_laudos: DataFrame,
    df_agregados: DataFrame,
    df_qualidade: DataFrame,
    df_rejeitados: DataFrame,
    id_execucao: str,
    processado_em: str,
    metadados: dict,
    destino,
    marcar_como_atual: bool = True,
):
    """Coleta a Gold no driver e publica o pacote processado (ver `pacote.py`).

    Mesmo código no Spark local e no Databricks — só o `destino` muda.
    """
    from . import pacote

    por_origem: dict = {}
    for linha in df_analises.groupBy("origem_dado").count().collect():
        por_origem.setdefault(linha["origem_dado"], {})["analises"] = linha["count"]
    for linha in df_laudos.groupBy("origem_dado").count().collect():
        por_origem.setdefault(linha["origem_dado"], {})["laudos"] = linha["count"]

    import json

    qualidade = {
        "metricas": [linha.asDict() for linha in df_qualidade.orderBy("origem_dado", "metrica").collect()],
        "amostra_rejeitados": [json.loads(x) for x in para_linhas_json(
            df_rejeitados.select("id_laudo", "origem_dado", "motivo_rejeicao",
                                 F.substring("registro_original", 1, 300).alias("trecho")),
            ["motivo_rejeicao", "id_laudo"])[:30]],
    }
    return pacote.publicar(
        id_execucao,
        analises=para_linhas_json(df_analises, ["data_analise_ts", "id_analise"]),
        laudos=para_linhas_json(df_laudos, ["data_analise_ts", "id_laudo"]),
        agregados=para_linhas_json(df_agregados, list(DIMENSOES_AGREGADO)),
        qualidade=qualidade,
        metadados={"processado_em": processado_em, "por_origem": por_origem, **metadados},
        destino=destino,
        marcar_como_atual=marcar_como_atual,
    )
