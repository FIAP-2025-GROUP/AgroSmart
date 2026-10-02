"""Regras de normalização da Silver, como tabelas de dados.

O CNN só emite as 12 classes de `src/rotulos.py`; o Gemini escreve texto livre
("Requeima (Phytophthora infestans)", "Míldio tardio", "tomateiro"...). Para
agrupar os dois num mesmo gráfico a Silver:

1. preserva sempre o texto recebido em `diagnostico_original`;
2. reduz cultura e diagnóstico a uma **chave** (minúsculas, sem acento, só
   letras e dígitos separados por um espaço);
3. procura sinônimos conhecidos dentro da chave, por palavra inteira, e fica com
   o sinônimo mais longo encontrado ("mildio tardio" vence "mildio");
4. sem sinônimo conhecido, mantém o texto original limpo e marca o registro como
   não mapeado — não força o texto livre para dentro do catálogo.

As tabelas ficam aqui, em Python puro, para serem usadas de dois jeitos: pelo
Spark (`transformacoes_spark` as transforma em DataFrames e faz join) e pelas
funções de referência abaixo, que servem aos testes e à documentação. A chave é
calculada com a mesma tabela de tradução nos dois lados para que os resultados
coincidam.
"""

from __future__ import annotations

import re

from .. import rotulos
from . import contratos as c

# Mesma tradução usada pelo `F.translate` do Spark.
ACENTOS = "áàâãäéèêëíìîïóòôõöúùûüçñ"
SEM_ACENTOS = "aaaaaeeeeiiiiooooouuuucn"
_TABELA = str.maketrans(ACENTOS, SEM_ACENTOS)

# Valores que a Fase 1 usa para "não se aplica" e que significam ausência.
VAZIOS = ("", "—", "-", "–", "n/a", "na", "none", "null")


def chave(texto: str | None) -> str | None:
    """Forma comparável de um texto: 'Míldio-Tardio!' -> 'mildio tardio'."""
    if texto is None:
        return None
    reduzido = str(texto).lower().translate(_TABELA)
    reduzido = re.sub(r"[^a-z0-9]+", " ", reduzido).strip()
    return reduzido or None


def limpar(texto: str | None) -> str | None:
    """Texto aparado; marcadores de vazio da Fase 1 viram None."""
    if texto is None:
        return None
    aparado = re.sub(r"\s+", " ", str(texto)).strip()
    return None if aparado.lower() in VAZIOS else aparado


# --------------------------------------------------------------------------- #
# Domínios simples
# --------------------------------------------------------------------------- #
CONDICAO_POR_CHAVE = {
    "saudavel": c.CONDICAO_SAUDAVEL,
    "sadia": c.CONDICAO_SAUDAVEL,
    "healthy": c.CONDICAO_SAUDAVEL,
    "doente": c.CONDICAO_DOENTE,
    "diseased": c.CONDICAO_DOENTE,
    "indeterminado": c.CONDICAO_INDETERMINADA,
    "indefinido": c.CONDICAO_INDETERMINADA,
    "inconclusivo": c.CONDICAO_INDETERMINADA,
}

# O motor é reconhecido por fragmento: "CNN especializado" e "Gemini (VLM)"
# são os rótulos gravados pela Fase 1 (`src/tipos.py`).
FRAGMENTOS_MOTOR = (("cnn", c.MOTOR_CNN), ("vlm", c.MOTOR_VLM), ("gemini", c.MOTOR_VLM))

# --------------------------------------------------------------------------- #
# Culturas
# --------------------------------------------------------------------------- #
# As cinco culturas do CNN usam exatamente o nome de `rotulos.CLASSES`.
_CULTURAS_CNN = sorted({classe["cultura"] for classe in rotulos.CLASSES})

SINONIMOS_CULTURA: dict[str, str] = {
    # catálogo do CNN
    "maca": "Maçã",
    "macieira": "Maçã",
    "malus domestica": "Maçã",
    "milho": "Milho",
    "zea mays": "Milho",
    "uva": "Uva",
    "videira": "Uva",
    "vitis vinifera": "Uva",
    "batata": "Batata",
    "batata inglesa": "Batata",
    "batateira": "Batata",
    "solanum tuberosum": "Batata",
    "tomate": "Tomate",
    "tomateiro": "Tomate",
    "solanum lycopersicum": "Tomate",
    # fora do catálogo — só o motor generalista diagnostica
    "cafe": "Café",
    "cafeeiro": "Café",
    "coffea arabica": "Café",
    "soja": "Soja",
    "glycine max": "Soja",
    "feijao": "Feijão",
    "feijoeiro": "Feijão",
    "phaseolus vulgaris": "Feijão",
    "laranja": "Citros",
    "laranjeira": "Citros",
    "limoeiro": "Citros",
    "citros": "Citros",
    "citrus": "Citros",
    "roseira": "Roseira",
    "rosa": "Roseira",
    "morango": "Morango",
    "morangueiro": "Morango",
    "alface": "Alface",
    # "batata doce" não é batata: o sinônimo mais longo vence e evita o engano.
    "batata doce": "Batata-doce",
}

assert set(_CULTURAS_CNN) <= set(SINONIMOS_CULTURA.values()), "cultura do CNN sem sinônimo"

# --------------------------------------------------------------------------- #
# Diagnósticos
# --------------------------------------------------------------------------- #
# chave do sinônimo -> (diagnóstico normalizado, categoria)
SINONIMOS_DIAGNOSTICO: dict[str, tuple[str, str]] = {}


def _registrar(nome: str, categoria: str, *sinonimos: str) -> None:
    for sinonimo in (nome, *sinonimos):
        SINONIMOS_DIAGNOSTICO[chave(sinonimo)] = (nome, categoria)


# Classes doentes do CNN, com o nome exato do catálogo.
for _classe in rotulos.CLASSES:
    if _classe["condicao"] == rotulos.CONDICAO_DOENTE:
        _categoria = c.CATEGORIA_PRAGA if _classe["agente"].startswith("Praga") else c.CATEGORIA_DOENCA
        _registrar(_classe["diagnostico"], _categoria)

# Variações de escrita das mesmas doenças vindas do texto livre do VLM.
_registrar("Sarna-da-macieira", c.CATEGORIA_DOENCA, "sarna da maca", "venturia inaequalis")
_registrar("Ferrugem comum", c.CATEGORIA_DOENCA, "ferrugem comum do milho", "puccinia sorghi")
_registrar("Podridão-negra", c.CATEGORIA_DOENCA, "podridao negra da videira", "black rot", "guignardia bidwellii")
_registrar("Pinta-preta", c.CATEGORIA_DOENCA, "alternariose", "mancha de alternaria", "alternaria solani", "early blight")
_registrar("Requeima", c.CATEGORIA_DOENCA, "mildio tardio", "phytophthora infestans", "late blight")
_registrar("Mancha de septória", c.CATEGORIA_DOENCA, "septoriose", "septoria lycopersici")
_registrar("Ácaro-rajado", c.CATEGORIA_PRAGA, "acaro rajado do tomateiro", "tetranychus urticae", "spider mite")

# Problemas comuns fora do catálogo do CNN.
_registrar("Ferrugem-do-cafeeiro", c.CATEGORIA_DOENCA, "hemileia vastatrix")
_registrar("Ferrugem-asiática", c.CATEGORIA_DOENCA, "ferrugem asiatica da soja", "phakopsora pachyrhizi")
_registrar("Cancro cítrico", c.CATEGORIA_DOENCA, "xanthomonas citri")
_registrar("Oídio", c.CATEGORIA_DOENCA, "oidio da roseira", "podosphaera pannosa")
_registrar("Mancha-angular", c.CATEGORIA_DOENCA, "mancha angular do feijoeiro")
_registrar("Antracnose", c.CATEGORIA_DOENCA)
_registrar("Mancha bacteriana", c.CATEGORIA_DOENCA)
_registrar("Mosca-branca", c.CATEGORIA_PRAGA, "bemisia tabaci")
_registrar("Pulgões", c.CATEGORIA_PRAGA, "pulgao", "afideos", "afidios")
_registrar("Tripes", c.CATEGORIA_PRAGA, "thrips")
_registrar("Lagarta-do-cartucho", c.CATEGORIA_PRAGA, "spodoptera frugiperda")
_registrar("Bicho-mineiro", c.CATEGORIA_PRAGA, "leucoptera coffeella")
_registrar("Deficiência de nitrogênio", c.CATEGORIA_OUTRA, "clorose por falta de nitrogenio")
_registrar("Deficiência de potássio", c.CATEGORIA_OUTRA)
_registrar("Queimadura solar", c.CATEGORIA_OUTRA, "escaldadura")

DIAGNOSTICO_SAUDAVEL = "Saudável"
DIAGNOSTICO_INDETERMINADO = "Indeterminado"

# Sem sinônimo, a categoria de um laudo "doente" é inferida por palavras-chave
# do agente e do próprio diagnóstico. A ordem importa: praga é testada antes de
# doença porque "Praga (Tetranychus urticae)" não deve cair em outra regra.
# Expressões compatíveis com Java (Spark `rlike`) e com Python (`re`).
PADROES_CATEGORIA: list[tuple[str, str]] = [
    (c.CATEGORIA_PRAGA, r"\b(praga|acaro|inseto|pulgao|pulgoes|lagarta|mosca|tripes|cochonilha|percevejo|broca|besouro|vaquinha)"),
    (c.CATEGORIA_DOENCA, r"\b(fungo|fungica|fungico|bacteria|bacteriana|bacteriose|virus|viral|virose|oomiceto|nematoide|fitoplasma|mildio|ferrugem|podridao|mancha)"),
    (c.CATEGORIA_OUTRA, r"\b(deficiencia|nutricional|abiotico|abiotica|queimadura|estresse|fitotoxicidade|toxicidade|geada|granizo|clorose)"),
]


def _melhor_sinonimo(chave_texto: str | None, tabela: dict) -> str | None:
    """Sinônimo mais longo contido na chave, por palavra inteira."""
    if not chave_texto:
        return None
    alvo = f" {chave_texto} "
    candidatos = [s for s in tabela if f" {s} " in alvo]
    return max(candidatos, key=lambda s: (len(s), s)) if candidatos else None


# --------------------------------------------------------------------------- #
# Funções de referência (o Spark implementa a mesma lógica com DataFrames)
# --------------------------------------------------------------------------- #
def normalizar_condicao(texto: str | None) -> str | None:
    return CONDICAO_POR_CHAVE.get(chave(texto) or "")


def normalizar_motor(texto: str | None) -> str | None:
    k = chave(texto) or ""
    for fragmento, motor in FRAGMENTOS_MOTOR:
        if fragmento in k.split():
            return motor
    return None


def normalizar_cultura(cultura: str | None, especie: str | None) -> tuple[str | None, bool]:
    """(cultura normalizada, se veio de um sinônimo conhecido)."""
    for texto in (cultura, especie):
        sinonimo = _melhor_sinonimo(chave(limpar(texto)), SINONIMOS_CULTURA)
        if sinonimo:
            return SINONIMOS_CULTURA[sinonimo], True
    original = limpar(cultura) or limpar(especie)
    return original, False


def categorizar_por_palavras(*textos: str | None) -> str:
    alvo = " ".join(k for k in (chave(t) for t in textos) if k)
    for categoria, padrao in PADROES_CATEGORIA:
        if re.search(padrao, alvo):
            return categoria
    return c.CATEGORIA_OUTRA


def normalizar_diagnostico(
    condicao: str | None, diagnostico: str | None, agente: str | None
) -> tuple[str | None, str | None, bool]:
    """(diagnóstico normalizado, categoria, se veio de um sinônimo conhecido).

    `condicao` já deve estar normalizada.
    """
    if condicao == c.CONDICAO_SAUDAVEL:
        return DIAGNOSTICO_SAUDAVEL, c.CATEGORIA_SAUDAVEL, True
    if condicao == c.CONDICAO_INDETERMINADA:
        return DIAGNOSTICO_INDETERMINADO, c.CATEGORIA_INDETERMINADA, True
    if condicao != c.CONDICAO_DOENTE:
        return None, None, False

    sinonimo = _melhor_sinonimo(chave(limpar(diagnostico)), SINONIMOS_DIAGNOSTICO)
    if sinonimo:
        nome, categoria = SINONIMOS_DIAGNOSTICO[sinonimo]
        return nome, categoria, True
    return limpar(diagnostico), categorizar_por_palavras(diagnostico, agente), False


def tabela_culturas() -> list[tuple[str, str]]:
    """(chave do sinônimo, cultura) — vira DataFrame no Spark."""
    return sorted(SINONIMOS_CULTURA.items())


def tabela_diagnosticos() -> list[tuple[str, str, str]]:
    """(chave do sinônimo, diagnóstico, categoria) — vira DataFrame no Spark."""
    return sorted((k, nome, cat) for k, (nome, cat) in SINONIMOS_DIAGNOSTICO.items())
