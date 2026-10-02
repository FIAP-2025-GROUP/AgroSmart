"""Gera laudos SIMULADOS para demonstrar o pipeline e o dashboard da Fase 2.

Execução:
    .venv\\Scripts\\python.exe ferramentas\\gerar_dados_simulados.py
    .venv\\Scripts\\python.exe ferramentas\\gerar_dados_simulados.py --semente 7 --saida outro.jsonl

Saída: `dados/simulados/laudos_simulados.jsonl`, um laudo por linha, já no
contrato da Fase 2 e com `origem_dado = "simulado"` em todas as linhas.

O que é e o que não é
---------------------
São dados **sintéticos**, gerados por regras e sorteio com semente fixa. Não
representam incidência real de doenças em nenhuma propriedade ou município — os
nomes de lugares só dão contexto geográfico verossímil ao exemplo. As
tendências (requeima no tomate subindo e depois cedendo, ferrugem do milho
crescendo em Cascavel, podridão-negra caindo na uva...) foram desenhadas para
que o dashboard tenha algo a mostrar.

Esses registros **não** vão para o histórico SQLite da Fase 1: entram direto
pela fronteira RAW (`ferramentas/exportar_snapshot.py --origem simulado`).

Fidelidade à Fase 1
-------------------
* Os laudos do CNN usam somente as 12 classes de `src/rotulos.py`, com os
  mesmos textos de sintomas e manejo, a mesma regra de limiar e o mesmo
  `confianca_texto` ("87.3%").
* Os laudos do VLM têm `confianca` nula e autoavaliação em palavras, como o
  `src/vlm.py` produz — e escrevem o diagnóstico em texto livre, com variações
  de grafia, para exercitar a normalização da Silver.
* Modo automático: o CNN responde; se ficar abaixo do limiar, só o laudo do VLM
  é emitido (o indeterminado do CNN é substituído). Modo comparação: dois laudos
  para a mesma análise.

Também são incluídas, de propósito, algumas imperfeições: reanálises da mesma
imagem, análises sem localidade completa ou sem `id_imagem`, e um punhado de
registros inválidos e duplicados que a Silver precisa rejeitar com motivo.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from src import rotulos  # noqa: E402
from src.pipeline import contratos as c  # noqa: E402
from src.tipos import MOTOR_CNN, MOTOR_VLM  # noqa: E402

SEMENTE_PADRAO = 20260901
INICIO = date(2026, 6, 1)  # segunda-feira
SEMANAS = 12
FUSO = timezone(timedelta(hours=-3))
NOME_FUSO = "America/Sao_Paulo"
FIM = datetime(INICIO.year, INICIO.month, INICIO.day, 18, 0, tzinfo=FUSO) + timedelta(days=7 * SEMANAS - 1)
INSTANCIA = "gerador-simulado"

MODELO_CNN = "MobileNetV2 v1.0.0"  # mesmo formato de `inferencia.carregar_modelo`
MODELO_VLM = "gemini-3.6-flash"  # `src/vlm.py::MODELO`

MODO_AUTO = "Automático (CNN → VLM)"  # textos de `src/diagnostico.py::MODOS`
MODO_CNN = "Somente CNN especializado"
MODO_VLM = "Somente visão generalista"
MODO_COMPARACAO = "Comparar os dois motores"

# --------------------------------------------------------------------------- #
# Cenário
# --------------------------------------------------------------------------- #
PROPRIEDADES = [
    {"uf": "SP", "municipio": "Campinas", "propriedade": "Sítio Boa Esperança",
     "talhoes": ["T01", "T02", "T03"], "culturas": {"Tomate": 0.65, "Batata": 0.35}, "peso": 1.3},
    {"uf": "MG", "municipio": "Pouso Alegre", "propriedade": "Fazenda Serra Verde",
     "talhoes": ["A1", "A2"], "culturas": {"Batata": 0.5, "Tomate": 0.2, "Café": 0.3}, "peso": 1.0},
    {"uf": "PR", "municipio": "Cascavel", "propriedade": "Fazenda Santa Clara",
     "talhoes": ["Gleba 1", "Gleba 2", "Gleba 3"], "culturas": {"Milho": 0.75, "Soja": 0.25}, "peso": 1.2},
    {"uf": "GO", "municipio": "Rio Verde", "propriedade": "Fazenda Três Lagoas",
     "talhoes": ["P1", "P2"], "culturas": {"Milho": 0.7, "Feijão": 0.3}, "peso": 0.9},
    {"uf": "RS", "municipio": "Bento Gonçalves", "propriedade": "Vinícola Vale Alto",
     "talhoes": ["Q1", "Q2"], "culturas": {"Uva": 1.0}, "peso": 0.8},
    {"uf": "SC", "municipio": "Fraiburgo", "propriedade": "Pomar Campo Alto",
     "talhoes": ["L1", "L2"], "culturas": {"Maçã": 1.0}, "peso": 0.7},
]

# Classe saudável e classes doentes do CNN para cada cultura do catálogo.
CLASSES_POR_CULTURA: dict[str, list[dict]] = {}
for _classe in rotulos.CLASSES:
    CLASSES_POR_CULTURA.setdefault(_classe["cultura"], []).append(_classe)


def _sigmoide(x: float) -> float:
    return 1 / (1 + math.exp(-x))


def prevalencias(cultura: str, municipio: str, semana: int) -> dict[str, float]:
    """Probabilidade de cada problema numa análise, por cultura, local e semana.

    Para culturas do catálogo as chaves são ids de `rotulos.CLASSES`; para as
    demais, nomes de problema que só o VLM reconhece. O que sobra é saudável.
    """
    s = semana
    if cultura == "Tomate":
        pico = 0.32 if municipio == "Campinas" else 0.14
        requeima = 0.04 + pico * math.exp(-((s - 4.5) ** 2) / 6.0)  # sobe e cede
        acaro = 0.02 + 0.22 * _sigmoide((s - 8) / 1.0)  # aparece no fim (seco)
        return {"tomato_late_blight": requeima, "tomato_spider_mites": acaro, "tomato_septoria": 0.07}
    if cultura == "Batata":
        return {"potato_early_blight": 0.06 + 0.022 * s}  # cresce de forma linear
    if cultura == "Milho":
        if municipio == "Cascavel":
            return {"corn_common_rust": 0.05 + 0.36 * _sigmoide((s - 5) / 1.3)}
        return {"corn_common_rust": 0.05 + 0.01 * s}
    if cultura == "Uva":
        return {"grape_black_rot": max(0.06, 0.32 - 0.024 * s)}  # cai ao longo do período (cenário simulado)
    if cultura == "Maçã":
        return {"apple_scab": 0.14 + 0.08 * math.exp(-((s - 2) ** 2) / 4.0)}
    if cultura == "Café":
        return {"ferrugem_cafe": 0.22, "bicho_mineiro": 0.04 + 0.012 * s}
    if cultura == "Soja":
        return {"ferrugem_asiatica": 0.05 + 0.03 * s, "percevejo": 0.06}
    if cultura == "Feijão":
        return {"mancha_angular": 0.10, "mosca_branca": 0.04 + 0.015 * s, "deficiencia_n": 0.12}
    return {}


# Como o VLM descreve cada problema (variações de grafia de propósito).
TEXTOS_VLM: dict[str, dict] = {
    "tomato_late_blight": {"diag": ["Requeima (Phytophthora infestans)", "Requeima", "Míldio tardio"],
                           "agente": "Oomiceto (Phytophthora infestans)"},
    "tomato_septoria": {"diag": ["Septoriose", "Mancha de Septoria"], "agente": "Fungo (Septoria lycopersici)"},
    "tomato_spider_mites": {"diag": ["Ácaro-rajado (Tetranychus urticae)", "Infestação de ácaros"],
                            "agente": "Praga (ácaro)"},
    "potato_early_blight": {"diag": ["Pinta-preta (Alternaria solani)", "Alternariose", "Mancha de alternária"],
                            "agente": "Fungo (Alternaria solani)"},
    "corn_common_rust": {"diag": ["Ferrugem comum do milho", "Ferrugem comum (Puccinia sorghi)"],
                         "agente": "Fungo (Puccinia sorghi)"},
    "grape_black_rot": {"diag": ["Podridão-negra da videira", "Podridão negra (Guignardia bidwellii)"],
                        "agente": "Fungo (Guignardia bidwellii)"},
    "apple_scab": {"diag": ["Sarna-da-macieira (Venturia inaequalis)", "Sarna da macieira"],
                   "agente": "Fungo (Venturia inaequalis)"},
    "ferrugem_cafe": {"diag": ["Ferrugem-do-cafeeiro (Hemileia vastatrix)", "Ferrugem do cafeeiro"],
                      "agente": "Fungo (Hemileia vastatrix)"},
    "bicho_mineiro": {"diag": ["Bicho-mineiro (Leucoptera coffeella)"], "agente": "Praga (lepidóptero minador)"},
    "ferrugem_asiatica": {"diag": ["Ferrugem-asiática da soja", "Ferrugem asiática (Phakopsora pachyrhizi)"],
                          "agente": "Fungo (Phakopsora pachyrhizi)"},
    "percevejo": {"diag": ["Dano por percevejo-marrom"], "agente": "Praga (Euschistus heros)"},
    "mancha_angular": {"diag": ["Mancha-angular do feijoeiro"], "agente": "Fungo (Pseudocercospora griseola)"},
    "mosca_branca": {"diag": ["Mosca-branca (Bemisia tabaci)"], "agente": "Praga (Bemisia tabaci)"},
    "deficiencia_n": {"diag": ["Deficiência de nitrogênio", "Clorose por falta de nitrogênio"],
                      "agente": "Deficiência nutricional"},
}

ESPECIE_VLM = {
    "Tomate": ["Tomateiro (Solanum lycopersicum)", "Tomate", "Tomateiro"],
    "Batata": ["Batata (Solanum tuberosum)", "Batateira"],
    "Milho": ["Milho (Zea mays)", "Milho"],
    "Uva": ["Videira (Vitis vinifera)", "Videira"],
    "Maçã": ["Macieira (Malus domestica)", "Macieira"],
    "Café": ["Cafeeiro (Coffea arabica)", "Café"],
    "Soja": ["Soja (Glycine max)", "Soja"],
    "Feijão": ["Feijoeiro (Phaseolus vulgaris)", "Feijão"],
}

SINTOMAS_VLM = {
    "saudável": "Folhas com coloração uniforme, sem lesões, pústulas ou sinais de insetos.",
    "doente": "Lesões e alterações de cor visíveis no limbo foliar, compatíveis com o diagnóstico.",
    "indeterminado": "Imagem com foco ou enquadramento insuficiente para uma conclusão segura.",
}
MANEJO_VLM = {
    "saudável": "Manter o monitoramento periódico e as práticas culturais de rotina.",
    "doente": ("Remover o material mais afetado, melhorar a ventilação e consultar um engenheiro "
               "agrônomo antes de qualquer controle químico."),
    "indeterminado": "Fotografar novamente uma folha afetada, de perto e com luz difusa.",
}


# --------------------------------------------------------------------------- #
# Sorteio
# --------------------------------------------------------------------------- #
def _escolher(rng: random.Random, pesos: dict):
    return rng.choices(list(pesos), weights=list(pesos.values()), k=1)[0]


def _id_imagem(semente: int, numero: int) -> str:
    return "sim-img-" + hashlib.sha1(f"{semente}:{numero}".encode()).hexdigest()[:12]


def _laudo_base(analise: dict) -> dict:
    registro = c.registro_vazio()
    for campo in ("id_analise", "id_imagem", "arquivo", "uf", "municipio", "propriedade", "talhao",
                  "modo_solicitado", "limiar_cnn", "tipo_amostra"):
        registro[campo] = analise[campo]
    registro.update(versao_schema=c.VERSAO_SCHEMA, origem_instancia=INSTANCIA,
                    origem_dado=c.ORIGEM_SIMULADO, fuso_origem=NOME_FUSO)
    return registro


def laudo_cnn(rng: random.Random, analise: dict, instante: datetime) -> dict:
    """Laudo do CNN pela mesma regra de `src/inferencia.py`."""
    limiar = analise["limiar_cnn"]
    cultura, verdade = analise["cultura_real"], analise["problema_real"]
    registro = _laudo_base(analise)
    registro.update(motor=MOTOR_CNN, modelo_versao=MODELO_CNN, data_analise=instante.isoformat())

    no_catalogo = cultura in CLASSES_POR_CULTURA
    if not no_catalogo or rng.random() < 0.07:  # fora do domínio ou foto ruim
        confianca = rng.uniform(0.22, limiar - 0.02)
        registro.update(
            especie_original="—", diagnostico_original="Indeterminado",
            condicao=rotulos.CONDICAO_INDETERMINADA, agente_original="—",
            confianca=round(confianca, 4), confianca_texto=f"{confianca:.1%}",
            sintomas=("Imagem fora do domínio treinado ou de baixa qualidade: nenhuma das 12 classes "
                      f"atingiu a confiança mínima de {limiar:.0%}."),
            manejo=("Esta cultura provavelmente não está entre as 12 classes do modelo especializado. "
                    "Use o motor de visão generalista para analisar esta imagem."),
        )
        return registro

    previsto = verdade
    if rng.random() < 0.07:  # erro de classificação dentro da mesma cultura
        previsto = rng.choice([k["id"] for k in CLASSES_POR_CULTURA[cultura] if k["id"] != verdade])
    confianca = max(limiar + 0.01, 1 - (rng.random() ** 2) * 0.36)
    info = rotulos.descrever(previsto)
    registro.update(
        especie_original=info["cultura"], diagnostico_original=info["diagnostico"],
        condicao=info["condicao"], agente_original=info["agente"],
        confianca=round(confianca, 4), confianca_texto=f"{confianca:.1%}",
        sintomas=info["sintomas"], manejo=info["manejo"],
    )
    return registro


def laudo_vlm(rng: random.Random, analise: dict, instante: datetime) -> dict:
    """Laudo do generalista: texto livre e confiança só em palavras."""
    cultura, verdade = analise["cultura_real"], analise["problema_real"]
    registro = _laudo_base(analise)
    registro.update(motor=MOTOR_VLM, modelo_versao=MODELO_VLM, data_analise=instante.isoformat(),
                    especie_original=rng.choice(ESPECIE_VLM[cultura]))

    saudavel = verdade is None or verdade.endswith("_healthy")
    sorteio = rng.random()
    if saudavel:
        condicao = "saudável" if sorteio < 0.9 else "indeterminado"
    else:
        condicao = "doente" if sorteio < 0.86 else ("indeterminado" if sorteio < 0.96 else "saudável")

    if condicao == "doente":
        textos = TEXTOS_VLM[verdade]
        diagnostico, agente = rng.choice(textos["diag"]), textos["agente"]
    else:
        diagnostico, agente = ("Saudável" if condicao == "saudável" else "Indeterminado"), "—"

    nivel = _escolher(rng, {"alta": 0.55, "média": 0.35, "baixa": 0.10})
    if condicao == "indeterminado":
        nivel = "baixa"
    registro.update(
        condicao=condicao, diagnostico_original=diagnostico, agente_original=agente,
        confianca=None, confianca_texto=f"{nivel} (autoavaliada)",
        sintomas=SINTOMAS_VLM[condicao], manejo=MANEJO_VLM[condicao],
        observacoes="Parte analisada: folha",
    )
    return registro


def gerar(semente: int = SEMENTE_PADRAO) -> list[dict]:
    rng = random.Random(semente)
    pesos_prop = {i: p["peso"] for i, p in enumerate(PROPRIEDADES)}
    analises: list[dict] = []
    laudos: list[dict] = []
    contador_laudo = 0

    for semana in range(SEMANAS):
        volume = round(92 * (0.8 + 0.045 * semana))  # adoção crescente
        for _ in range(volume):
            numero = len(analises) + 1
            prop = PROPRIEDADES[_escolher(rng, pesos_prop)]
            dia = INICIO + timedelta(days=7 * semana + rng.randrange(7))
            instante = datetime(dia.year, dia.month, dia.day, rng.randint(6, 18),
                                rng.randrange(60), rng.randrange(60), tzinfo=FUSO)

            reanalise = analises and rng.random() < 0.06
            if reanalise:  # mesma foto enviada de novo alguns dias depois
                anterior = rng.choice(analises[-120:])
                cultura, problema = anterior["cultura_real"], anterior["problema_real"]
                id_imagem, arquivo = anterior["id_imagem"], anterior["arquivo"]
                prop = anterior["prop"]
                talhao = anterior["talhao"]
                instante = max(instante, anterior["instante"] + timedelta(hours=rng.randint(2, 72)))
                instante = min(instante, FIM)  # não transborda a janela simulada
            else:
                cultura = _escolher(rng, prop["culturas"])
                riscos = prevalencias(cultura, prop["municipio"], semana)
                sorteio, acumulado, problema = rng.random(), 0.0, None
                for chave_problema, p in riscos.items():
                    acumulado += p
                    if sorteio < acumulado:
                        problema = chave_problema
                        break
                if problema is None and cultura in CLASSES_POR_CULTURA:
                    problema = next(k["id"] for k in CLASSES_POR_CULTURA[cultura]
                                    if k["condicao"] == rotulos.CONDICAO_SAUDAVEL)
                id_imagem = _id_imagem(semente, numero) if rng.random() > 0.03 else None
                arquivo = f"IMG_{dia:%Y%m%d}_{numero:04d}.jpg"
                talhao = rng.choice(prop["talhoes"])

            no_catalogo = cultura in CLASSES_POR_CULTURA
            if no_catalogo:
                modo = _escolher(rng, {MODO_AUTO: 0.70, MODO_COMPARACAO: 0.15, MODO_CNN: 0.10, MODO_VLM: 0.05})
            else:
                modo = _escolher(rng, {MODO_AUTO: 0.85, MODO_VLM: 0.15})

            local = {k: prop[k] for k in ("uf", "municipio", "propriedade")}
            lacuna = rng.random()
            if lacuna < 0.02:  # nenhuma localidade informada
                local = {"uf": None, "municipio": None, "propriedade": None}
                talhao = None
            elif lacuna < 0.06:  # só a UF
                local = {"uf": prop["uf"], "municipio": None, "propriedade": None}
                talhao = None

            analise = {
                "id_analise": f"sim-{semente}-an-{numero:05d}", "id_imagem": id_imagem, "arquivo": arquivo,
                **local, "talhao": talhao, "modo_solicitado": modo,
                "limiar_cnn": 0.70 if rng.random() < 0.1 else rotulos.LIMIAR_CONFIANCA,
                "tipo_amostra": c.TIPO_FOTO_CAMPO if rng.random() < 0.92 else c.TIPO_IMAGEM_REFERENCIA,
                "cultura_real": cultura, "problema_real": problema,
                "prop": prop, "instante": instante,
            }
            if modo == MODO_VLM:
                analise["limiar_cnn"] = None  # o CNN não roda
            analises.append(analise)

            emitidos: list[dict] = []
            if modo in (MODO_AUTO, MODO_CNN, MODO_COMPARACAO):
                cnn = laudo_cnn(rng, analise, instante)
                indeterminado = cnn["condicao"] == rotulos.CONDICAO_INDETERMINADA
                if modo == MODO_AUTO and indeterminado:
                    emitidos.append(laudo_vlm(rng, analise, instante + timedelta(seconds=rng.randint(2, 6))))
                else:
                    emitidos.append(cnn)
                if modo == MODO_COMPARACAO:
                    emitidos.append(laudo_vlm(rng, analise, instante + timedelta(seconds=rng.randint(2, 6))))
            else:
                emitidos.append(laudo_vlm(rng, analise, instante + timedelta(seconds=rng.randint(2, 6))))

            for laudo in emitidos:
                contador_laudo += 1
                laudo["id_laudo"] = f"sim-{semente}-ld-{contador_laudo:05d}"
                laudos.append(laudo)

    laudos.sort(key=lambda r: (r["data_analise"], r["id_laudo"]))
    return laudos + registros_invalidos(laudos, semente)


def registros_invalidos(validos: list[dict], semente: int = SEMENTE_PADRAO) -> list[dict]:
    """Imperfeições intencionais que a Silver deve rejeitar com motivo."""
    marca = "Registro inválido inserido de propósito para demonstrar a validação da Silver."
    defeitos = [
        {"confianca": 1.37, "confianca_texto": "137.0%"},
        {"confianca": -0.2, "confianca_texto": "-20.0%"},
        {"data_analise": "31/02/2026 10:00"},
        {"data_analise": "ontem à tarde"},
        {"motor": "Motor desconhecido"},
        {"condicao": "talvez"},
        {"id_laudo": None},
    ]
    saida = []
    base = [r for r in validos if r["motor"] == MOTOR_CNN][:len(defeitos)]
    for numero, (original, defeito) in enumerate(zip(base, defeitos), start=1):
        registro = {**original, **defeito, "observacoes": marca}
        if "id_laudo" not in defeito:
            registro["id_laudo"] = f"sim-{semente}-ld-invalido-{numero:02d}"
        saida.append(registro)
    # Duplicados exatos: o mesmo laudo exportado duas vezes.
    saida.extend(dict(r) for r in validos[100:103])
    return saida


def main() -> None:
    analisador = argparse.ArgumentParser(description="Gera laudos simulados do AgroSmart (Fase 2).")
    analisador.add_argument("--semente", type=int, default=SEMENTE_PADRAO)
    analisador.add_argument("--saida", type=Path, default=c.ARQUIVO_SIMULADOS)
    argumentos = analisador.parse_args()

    registros = gerar(argumentos.semente)
    argumentos.saida.parent.mkdir(parents=True, exist_ok=True)
    with open(argumentos.saida, "w", encoding="utf-8", newline="\n") as arquivo:
        for registro in registros:
            arquivo.write(json.dumps(registro, ensure_ascii=False) + "\n")

    analises = len({r["id_analise"] for r in registros})
    print(f"{len(registros)} laudos simulados ({analises} análises) em {argumentos.saida}")
    print(f"Semente {argumentos.semente} · {INICIO:%d/%m/%Y} + {SEMANAS} semanas · origem_dado='simulado'")


if __name__ == "__main__":
    main()
