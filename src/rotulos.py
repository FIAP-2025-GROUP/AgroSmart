"""Catálogo de classes do modelo AgroSmart.

Fonte única da verdade sobre o significado de cada saída da rede. O notebook de
treino filtra o PlantVillage a partir dos padrões declarados aqui e exporta a
ordem final em `modelo/classes.json`; o app traduz o índice previsto em
diagnóstico, condição e recomendação de manejo usando o dicionário CLASSES.

A ordem da lista CLASSES define os índices 0..11 da camada de saída. Não
reordene sem retreinar o modelo.
"""

from __future__ import annotations

# Abaixo deste valor de probabilidade a predição é tratada como fora do domínio
# treinado. Sem isso, uma foto de roseira (que não existe no PlantVillage)
# receberia um diagnóstico de tomate com falsa confiança.
LIMIAR_CONFIANCA = 0.60

CONDICAO_SAUDAVEL = "saudável"
CONDICAO_DOENTE = "doente"
CONDICAO_INDETERMINADA = "indeterminado"

# Versão do catálogo. Vai junto nos arquivos exportados para rastreabilidade.
VERSAO_CATALOGO = "1.0.0"

# `padroes`: fragmentos usados pelo notebook para casar com os nomes de rótulo do
# TFDS. A comparação é feita sobre o nome normalizado (minúsculas, apenas
# alfanuméricos), então variações de pontuação entre versões do dataset não
# quebram o filtro.
CLASSES: list[dict] = [
    {
        "id": "apple_healthy",
        "padroes": ["apple", "healthy"],
        "cultura": "Maçã",
        "diagnostico": "Saudável",
        "condicao": CONDICAO_SAUDAVEL,
        "agente": "—",
        "sintomas": "Folha com coloração uniforme, sem lesões, manchas ou deformações no limbo.",
        "manejo": "Manter o monitoramento periódico e as práticas culturais de rotina.",
    },
    {
        "id": "apple_scab",
        "padroes": ["apple", "scab"],
        "cultura": "Maçã",
        "diagnostico": "Sarna-da-macieira",
        "condicao": CONDICAO_DOENTE,
        "agente": "Fungo (Venturia inaequalis)",
        "sintomas": "Manchas oliva a marrom-escuras, de aspecto aveludado, que evoluem para lesões necróticas.",
        "manejo": "Eliminar folhas caídas, melhorar a ventilação da copa por poda e aplicar fungicida preventivo nos períodos úmidos.",
    },
    {
        "id": "corn_healthy",
        "padroes": ["corn", "healthy"],
        "cultura": "Milho",
        "diagnostico": "Saudável",
        "condicao": CONDICAO_SAUDAVEL,
        "agente": "—",
        "sintomas": "Folha verde uniforme, sem pústulas ou estrias ao longo das nervuras.",
        "manejo": "Manter o monitoramento periódico e as práticas culturais de rotina.",
    },
    {
        "id": "corn_common_rust",
        "padroes": ["corn", "common", "rust"],
        "cultura": "Milho",
        "diagnostico": "Ferrugem comum",
        "condicao": CONDICAO_DOENTE,
        "agente": "Fungo (Puccinia sorghi)",
        "sintomas": "Pústulas pulverulentas marrom-avermelhadas nas duas faces da folha, que rompem a epiderme.",
        "manejo": "Priorizar híbridos resistentes e aplicar fungicida no aparecimento das primeiras pústulas.",
    },
    {
        "id": "grape_healthy",
        "padroes": ["grape", "healthy"],
        "cultura": "Uva",
        "diagnostico": "Saudável",
        "condicao": CONDICAO_SAUDAVEL,
        "agente": "—",
        "sintomas": "Limbo íntegro, sem manchas concêntricas nem bordas necrosadas.",
        "manejo": "Manter o monitoramento periódico e as práticas culturais de rotina.",
    },
    {
        "id": "grape_black_rot",
        "padroes": ["grape", "black", "rot"],
        "cultura": "Uva",
        "diagnostico": "Podridão-negra",
        "condicao": CONDICAO_DOENTE,
        "agente": "Fungo (Guignardia bidwellii)",
        "sintomas": "Manchas circulares pardas com borda escura e pontuações negras (picnídios) no centro.",
        "manejo": "Remover e destruir bagas mumificadas e ramos infectados; aplicar fungicida do início da brotação até a formação dos cachos.",
    },
    {
        "id": "potato_healthy",
        "padroes": ["potato", "healthy"],
        "cultura": "Batata",
        "diagnostico": "Saudável",
        "condicao": CONDICAO_SAUDAVEL,
        "agente": "—",
        "sintomas": "Folíolos verdes e túrgidos, sem lesões concêntricas ou áreas encharcadas.",
        "manejo": "Manter o monitoramento periódico e as práticas culturais de rotina.",
    },
    {
        "id": "potato_early_blight",
        "padroes": ["potato", "early", "blight"],
        "cultura": "Batata",
        "diagnostico": "Pinta-preta",
        "condicao": CONDICAO_DOENTE,
        "agente": "Fungo (Alternaria solani)",
        "sintomas": "Lesões escuras com anéis concêntricos, em alvo, começando pelas folhas mais velhas.",
        "manejo": "Fazer rotação de cultura, manter a adubação equilibrada e aplicar fungicida protetor no início dos sintomas.",
    },
    {
        "id": "tomato_healthy",
        "padroes": ["tomato", "healthy"],
        "cultura": "Tomate",
        "diagnostico": "Saudável",
        "condicao": CONDICAO_SAUDAVEL,
        "agente": "—",
        "sintomas": "Folíolos verdes uniformes, sem manchas, encharcamento ou pontuações claras.",
        "manejo": "Manter o monitoramento periódico e as práticas culturais de rotina.",
    },
    {
        "id": "tomato_late_blight",
        "padroes": ["tomato", "late", "blight"],
        "cultura": "Tomate",
        "diagnostico": "Requeima",
        "condicao": CONDICAO_DOENTE,
        "agente": "Oomiceto (Phytophthora infestans)",
        "sintomas": "Manchas grandes de aspecto encharcado, verde-escuras a marrons, com mofo esbranquiçado na face inferior.",
        "manejo": "Doença de evolução rápida em clima úmido e ameno: eliminar plantas afetadas, reduzir a molhagem foliar e aplicar fungicida sistêmico com urgência.",
    },
    {
        "id": "tomato_septoria",
        "padroes": ["tomato", "septoria"],
        "cultura": "Tomate",
        "diagnostico": "Mancha de septória",
        "condicao": CONDICAO_DOENTE,
        "agente": "Fungo (Septoria lycopersici)",
        "sintomas": "Muitas manchas pequenas e circulares, de centro claro e borda escura, iniciando nas folhas baixeiras.",
        "manejo": "Retirar as folhas baixeiras afetadas, evitar irrigação por aspersão e aplicar fungicida protetor.",
    },
    {
        "id": "tomato_spider_mites",
        "padroes": ["tomato", "spider", "mite"],
        "cultura": "Tomate",
        "diagnostico": "Ácaro-rajado",
        "condicao": CONDICAO_DOENTE,
        "agente": "Praga (Tetranychus urticae)",
        "sintomas": "Pontuações claras e finas por toda a folha, que amarelece e seca; teia fina na face inferior.",
        "manejo": "Praga favorecida por tempo quente e seco: elevar a umidade, preservar ácaros predadores e recorrer a acaricida específico apenas se o nível de dano exigir.",
    },
]

# Índice auxiliar: id da classe -> metadados completos.
POR_ID: dict[str, dict] = {classe["id"]: classe for classe in CLASSES}

# Ordem canônica dos índices de saída da rede.
IDS_ORDENADOS: list[str] = [classe["id"] for classe in CLASSES]


def normalizar(nome: str) -> str:
    """Reduz um rótulo do dataset a letras e dígitos minúsculos.

    `Corn_(maize)___Common_rust_` e `Corn___Common rust` colapsam para a mesma
    string, o que torna o filtro do notebook imune a diferenças de pontuação
    entre versões do PlantVillage.
    """
    return "".join(caractere for caractere in nome.lower() if caractere.isalnum())


def casar_rotulo(nome_dataset: str) -> str | None:
    """Devolve o id da classe AgroSmart correspondente a um rótulo do dataset.

    Retorna None quando o rótulo não pertence ao subconjunto de 12 classes.
    """
    normalizado = normalizar(nome_dataset)
    for classe in CLASSES:
        if all(normalizar(padrao) in normalizado for padrao in classe["padroes"]):
            return classe["id"]
    return None


def descrever(id_classe: str) -> dict:
    """Metadados de uma classe, com erro explícito se o id for desconhecido."""
    try:
        return POR_ID[id_classe]
    except KeyError:
        raise KeyError(
            f"Classe '{id_classe}' não existe no catálogo. O modelo em modelo/ "
            f"provavelmente foi treinado com um catálogo diferente deste "
            f"(versão atual: {VERSAO_CATALOGO})."
        ) from None
