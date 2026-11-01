"""Regras de decisão da Fase 3 — lógica booleana SE/ENTÃO.

Cada regra é uma função **pura**: recebe os dados já agregados e um booleano
dizendo se o mesmo episódio já foi alertado, e devolve um `Disparo` (ou None).
Quem busca os dados no banco é o `motor`; aqui só há a decisão, o que deixa
cada regra testável linha a linha contra a tabela de decisão do documento.

Regras (limites em `contratos.LIMITES`):

R1  Infestação por diagnóstico de imagem                     latência: diária
    SE   análises_conclusivas_7d(talhão) ≥ 5
    AND  doentes_7d / análises_conclusivas_7d > 30%
    AND  NOT alerta_R1_recente(talhão, 7 dias)
    ENTÃO alerta de infestação → e-mail + relatório de intervenção

R2  Mesmo problema em 3 dias seguidos                         latência: diária
    SE   problema P (doença OR praga) diagnosticado no talhão em D
    AND  P diagnosticado em D-1
    AND  P diagnosticado em D-2
    AND  NOT plano_de_controle_recente(talhão, P, 14 dias)
    ENTÃO acionar plano de controle → relatório (plano) + e-mail

R3  Irrigação com histerese                                   latência: minutos
    LIGAR    SE umidade_solo < 25% AND chuva_24h < 5 mm AND NOT irrigação_ligada
    DESLIGAR SE irrigação_ligada AND (umidade_solo ≥ 35% OR chuva_24h ≥ 5 mm)
    CRÍTICO  SE LIGAR AND umidade_solo < 15%  → também e-mail
    ENTÃO comando ao simulador de irrigação

R4  Clima favorável à requeima                                latência: horas
    SE   cultura ∈ {Tomate, Batata}
    AND  UR ≥ 90% AND 10 °C ≤ T ≤ 25 °C nas 2 últimas leituras seguidas
    AND  NOT alerta_R4_recente(talhão, 24 h)
    ENTÃO alerta preventivo + recomendação no painel

R5  Pragas na armadilha                                       latência: horas
    SE   (pragas ≥ 20)
         OR (pragas ≥ 10 AND laudo_de_praga(talhão, últimos 3 dias))
    AND  NOT alerta_R5_recente(talhão, 24 h)
    ENTÃO alerta de praga (e-mail se ≥ 20, o nível de controle)
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import date, timedelta

from .. import rotulos
from ..pipeline import contratos as c2
from .contratos import LIMITES as L

SEV_CRITICA, SEV_ALTA, SEV_MEDIA, SEV_INFO = "critica", "alta", "media", "informativa"

ACAO_ARQUIVO = "arquivo_alerta"
ACAO_EMAIL = "email"
ACAO_RELATORIO = "relatorio"
ACAO_IRRIGACAO = "irrigacao"
ORDEM_ACOES = (ACAO_ARQUIVO, ACAO_RELATORIO, ACAO_IRRIGACAO, ACAO_EMAIL)  # o e-mail anexa o relatório


@dataclass(frozen=True)
class Regra:
    id: str
    nome: str
    fonte: str
    expressao: str
    acoes: str
    latencia: str


CATALOGO = [
    Regra("R1", "Alerta de infestação", "laudos de imagem",
          f"análises_conclusivas_7d ≥ {L['r1_min_analises']} AND doentes_7d/análises_7d > "
          f"{L['r1_pct_doente']:.0%} AND NOT alerta_R1_nos_últimos_{L['r1_carencia_dias']}d",
          "arquivo de alerta + relatório de intervenção + e-mail", "diária (janela de 7 dias)"),
    Regra("R2", "Plano de controle por persistência", "laudos de imagem",
          "P ∈ {doença, praga} AND P(D) AND P(D-1) AND P(D-2) AND NOT plano_P_nos_últimos_"
          f"{L['r2_carencia_dias']}d",
          "arquivo de alerta + relatório (plano de controle) + e-mail", "diária (3 dias seguidos)"),
    Regra("R3", "Simulador de irrigação", "sensores",
          f"LIGAR: umidade_solo < {L['r3_liga_abaixo_pct']:.0f}% AND chuva_24h < "
          f"{L['r3_chuva_suficiente_mm']:.0f} mm AND NOT ligada · DESLIGAR: ligada AND (umidade_solo ≥ "
          f"{L['r3_desliga_acima_pct']:.0f}% OR chuva_24h ≥ {L['r3_chuva_suficiente_mm']:.0f} mm)",
          "comando ao simulador + arquivo de alerta (+ e-mail se umidade < "
          f"{L['r3_critico_abaixo_pct']:.0f}%)", "minutos (a cada leitura)"),
    Regra("R4", "Risco climático de requeima", "sensores",
          f"cultura ∈ {{Tomate, Batata}} AND UR ≥ {L['r4_ur_min_pct']:.0f}% AND "
          f"{L['r4_temp_min_c']:.0f} °C ≤ T ≤ {L['r4_temp_max_c']:.0f} °C em "
          f"{L['r4_leituras_seguidas']} leituras seguidas AND NOT alerta_R4_nas_últimas_"
          f"{L['r4_carencia_horas']}h",
          "arquivo de alerta + recomendação no painel", "horas"),
    Regra("R5", "Pragas na armadilha", "sensores + laudos",
          f"(pragas ≥ {L['r5_nivel_controle']} OR (pragas ≥ {L['r5_nivel_atencao']} AND "
          f"laudo_de_praga_{L['r5_janela_laudo_dias']}d)) AND NOT alerta_R5_nas_últimas_"
          f"{L['r5_carencia_horas']}h",
          f"arquivo de alerta + painel (+ e-mail se ≥ {L['r5_nivel_controle']})", "horas"),
]
POR_ID = {regra.id: regra for regra in CATALOGO}


@dataclass
class Disparo:
    regra: str
    severidade: str
    chave_dedup: str
    titulo: str
    mensagem: str
    recomendacao: str
    instante_evento: str
    local: dict
    evidencias: dict = field(default_factory=dict)
    acoes: tuple[str, ...] = (ACAO_ARQUIVO,)
    comando_irrigacao: str | None = None  # "ligar" / "desligar"


def _local(base: dict) -> dict:
    return {k: base.get(k) for k in ("uf", "municipio", "propriedade", "talhao", "cultura")}


def _onde(local: dict) -> str:
    return f"{local.get('propriedade')} / talhão {local.get('talhao')}"


def manejo_do_catalogo(diagnostico: str | None, cultura: str | None = None) -> str | None:
    """Recomendação de manejo do catálogo da Fase 1 para um diagnóstico."""
    candidatos = [k for k in rotulos.CLASSES if k["diagnostico"] == diagnostico]
    preferido = [k for k in candidatos if k["cultura"] == cultura] or candidatos
    return preferido[0]["manejo"] if preferido else None


# --------------------------------------------------------------------------- #
# Laudo principal por análise (mesma regra da Fase 2)
# --------------------------------------------------------------------------- #
def _prioridade(laudo: dict) -> int:
    conclusivo = laudo["condicao"] != c2.CONDICAO_INDETERMINADA
    if laudo["motor"] == c2.MOTOR_CNN:
        return 0 if conclusivo else 2
    return 1 if conclusivo else 3


def analises(laudos: list[dict]) -> list[dict]:
    """Uma linha por análise: o laudo principal (CNN conclusivo, senão VLM...).

    No modo comparação a mesma foto tem dois laudos; contar os dois inflaria a
    proporção de doentes. Laudos sem `id_analise` contam como uma análise cada.
    """
    grupos: dict[str, list[dict]] = {}
    for laudo in laudos:
        grupos.setdefault(laudo.get("id_analise") or f"laudo:{laudo['chave']}", []).append(laudo)
    return [min(g, key=lambda x: (_prioridade(x), x["instante"], x["chave"])) for g in grupos.values()]


# --------------------------------------------------------------------------- #
# R1 — infestação
# --------------------------------------------------------------------------- #
def r1_infestacao(dia: date, analises_janela: list[dict], ja_alertado: bool) -> Disparo | None:
    conclusivas = [a for a in analises_janela if a["condicao"] in (c2.CONDICAO_SAUDAVEL, c2.CONDICAO_DOENTE)]
    total = len(conclusivas)
    doentes = [a for a in conclusivas if a["condicao"] == c2.CONDICAO_DOENTE]
    proporcao = len(doentes) / total if total else 0.0

    dispara = (total >= L["r1_min_analises"]) and (proporcao > L["r1_pct_doente"]) and not ja_alertado
    if not dispara:
        return None

    local = _local(max(conclusivas, key=lambda a: a["instante"]))
    frequentes = Counter(a["diagnostico"] for a in doentes).most_common(3)
    principal = frequentes[0][0]
    manejo = manejo_do_catalogo(principal, local.get("cultura")) or next(
        (a["manejo"] for a in doentes if a["diagnostico"] == principal and a.get("manejo")), None)
    inicio = dia - timedelta(days=L["r1_janela_dias"] - 1)
    return Disparo(
        regra="R1", severidade=SEV_ALTA,
        chave_dedup=f"R1|{local['propriedade']}|{local['talhao']}|{dia.isoformat()}",
        titulo=f"Alerta de infestação — {_onde(local)}",
        mensagem=(f"{len(doentes)} de {total} análises conclusivas ({proporcao:.0%}) entre "
                  f"{inicio:%d/%m} e {dia:%d/%m} apontaram planta doente, acima do limite de "
                  f"{L['r1_pct_doente']:.0%}. Problema mais frequente: {principal}."),
        recomendacao=f"Manejo indicado para {principal}: {manejo or 'consultar o agrônomo responsável.'}",
        instante_evento=f"{dia.isoformat()}T23:59:59-03:00",
        local=local,
        evidencias={"janela": [inicio.isoformat(), dia.isoformat()], "analises_conclusivas": total,
                    "doentes": len(doentes), "proporcao_doente": round(proporcao, 4),
                    "limite": L["r1_pct_doente"], "diagnosticos": dict(frequentes)},
        acoes=(ACAO_ARQUIVO, ACAO_RELATORIO, ACAO_EMAIL),
    )


# --------------------------------------------------------------------------- #
# R2 — mesmo problema em dias seguidos
# --------------------------------------------------------------------------- #
def r2_persistencia(dia: date, problemas_por_dia: dict[date, dict[str, dict]],
                    ja_planejado) -> list[Disparo]:
    """`problemas_por_dia[d]` = {diagnóstico: análise de exemplo} com categoria doença/praga.

    `ja_planejado(diagnostico) -> bool` diz se já há plano recente para ele.
    """
    disparos = []
    n_dias = L["r2_dias_seguidos"]
    for diagnostico, exemplo in sorted(problemas_por_dia.get(dia, {}).items()):
        if exemplo["categoria"] not in (c2.CATEGORIA_DOENCA, c2.CATEGORIA_PRAGA):
            continue
        seguidos = all(diagnostico in problemas_por_dia.get(dia - timedelta(days=k), {})
                       for k in range(1, n_dias))
        if not (seguidos and not ja_planejado(diagnostico)):
            continue
        local = _local(exemplo)
        dias = [(dia - timedelta(days=k)).isoformat() for k in reversed(range(n_dias))]
        manejo = manejo_do_catalogo(diagnostico, local.get("cultura")) or exemplo.get("manejo")
        tipo = "praga" if exemplo["categoria"] == c2.CATEGORIA_PRAGA else "doença"
        disparos.append(Disparo(
            regra="R2", severidade=SEV_ALTA,
            chave_dedup=f"R2|{local['propriedade']}|{local['talhao']}|{diagnostico}|{dia.isoformat()}",
            titulo=f"Plano de controle acionado: {diagnostico} — {_onde(local)}",
            mensagem=(f"A {tipo} {diagnostico} foi diagnosticada no talhão em {n_dias} dias seguidos "
                      f"({', '.join(d[8:10] + '/' + d[5:7] for d in dias)}). A ocorrência deixou de ser "
                      f"pontual: o plano de controle foi acionado."),
            recomendacao=manejo or "Definir o controle com o agrônomo responsável.",
            instante_evento=f"{dia.isoformat()}T23:59:59-03:00",
            local=local,
            evidencias={"diagnostico": diagnostico, "categoria": exemplo["categoria"], "dias": dias},
            acoes=(ACAO_ARQUIVO, ACAO_RELATORIO, ACAO_EMAIL),
        ))
    return disparos


# --------------------------------------------------------------------------- #
# R3 — irrigação
# --------------------------------------------------------------------------- #
def r3_irrigacao(leitura: dict, chuva_24h: float, ligada: bool) -> Disparo | None:
    umidade = leitura["umidade_solo_pct"]
    seco = umidade < L["r3_liga_abaixo_pct"]
    choveu = chuva_24h >= L["r3_chuva_suficiente_mm"]
    umido = umidade >= L["r3_desliga_acima_pct"]

    ligar = seco and not choveu and not ligada
    desligar = ligada and (umido or choveu)
    if not (ligar or desligar):
        return None

    local = _local(leitura)
    base = {"id_leitura": leitura["id_leitura"], "umidade_solo_pct": umidade,
            "chuva_24h_mm": round(chuva_24h, 1), "estava_ligada": ligada}
    chave = f"R3|{local['propriedade']}|{local['talhao']}|{leitura['id_leitura']}"
    if ligar:
        critico = umidade < L["r3_critico_abaixo_pct"]
        return Disparo(
            regra="R3", severidade=SEV_CRITICA if critico else SEV_MEDIA, chave_dedup=chave,
            titulo=f"Irrigação LIGADA — {_onde(local)}",
            mensagem=(f"Umidade do solo em {umidade:.1f}% (limite {L['r3_liga_abaixo_pct']:.0f}%) e "
                      f"{chuva_24h:.1f} mm de chuva nas últimas 24 h: irrigação acionada."
                      + (" Nível crítico de umidade." if critico else "")),
            recomendacao=(f"Irrigar até a umidade do solo voltar a {L['r3_desliga_acima_pct']:.0f}%; "
                          "o desligamento é automático."),
            instante_evento=leitura["instante"], local=local, evidencias=base,
            acoes=(ACAO_ARQUIVO, ACAO_IRRIGACAO) + ((ACAO_EMAIL,) if critico else ()),
            comando_irrigacao="ligar",
        )
    motivo = "chuva suficiente" if choveu else "umidade do solo recuperada"
    return Disparo(
        regra="R3", severidade=SEV_INFO, chave_dedup=chave,
        titulo=f"Irrigação DESLIGADA — {_onde(local)}",
        mensagem=f"Umidade do solo em {umidade:.1f}% e {chuva_24h:.1f} mm de chuva em 24 h ({motivo}).",
        recomendacao="Nenhuma ação manual necessária.",
        instante_evento=leitura["instante"], local=local, evidencias=base,
        acoes=(ACAO_ARQUIVO, ACAO_IRRIGACAO), comando_irrigacao="desligar",
    )


# --------------------------------------------------------------------------- #
# R4 — clima favorável à requeima
# --------------------------------------------------------------------------- #
def clima_de_requeima(leitura: dict) -> bool:
    return (leitura["umidade_ar_pct"] >= L["r4_ur_min_pct"]
            and L["r4_temp_min_c"] <= leitura["temperatura_c"] <= L["r4_temp_max_c"])


def r4_requeima(ultimas: list[dict], ja_alertado: bool) -> Disparo | None:
    """`ultimas`: leituras do talhão em ordem cronológica, terminando na atual."""
    atual = ultimas[-1]
    n = L["r4_leituras_seguidas"]
    seguidas = len(ultimas) >= n and all(clima_de_requeima(x) for x in ultimas[-n:])
    dispara = (atual["cultura"] in L["r4_culturas"]) and seguidas and not ja_alertado
    if not dispara:
        return None
    local = _local(atual)
    janela = ultimas[-n:]
    return Disparo(
        regra="R4", severidade=SEV_MEDIA,
        chave_dedup=f"R4|{local['propriedade']}|{local['talhao']}|{atual['id_leitura']}",
        titulo=f"Risco climático de requeima — {_onde(local)}",
        mensagem=(f"{n} leituras seguidas com UR ≥ {L['r4_ur_min_pct']:.0f}% e temperatura entre "
                  f"{L['r4_temp_min_c']:.0f} e {L['r4_temp_max_c']:.0f} °C na cultura de {atual['cultura']}: "
                  "condição favorável à requeima (Phytophthora infestans)."),
        recomendacao=("Aplicação preventiva de fungicida protetor, evitar irrigação por aspersão e "
                      "intensificar a coleta de fotos do talhão nos próximos 3 dias."),
        instante_evento=atual["instante"], local=local,
        evidencias={"leituras": [{"id_leitura": x["id_leitura"], "instante": x["instante"],
                                  "umidade_ar_pct": x["umidade_ar_pct"], "temperatura_c": x["temperatura_c"]}
                                 for x in janela]},
        acoes=(ACAO_ARQUIVO,),
    )


# --------------------------------------------------------------------------- #
# R5 — pragas na armadilha
# --------------------------------------------------------------------------- #
def r5_pragas(leitura: dict, praga_em_laudo_recente: bool, ja_alertado: bool) -> Disparo | None:
    contagem = leitura["pragas_armadilha"]
    controle = contagem >= L["r5_nivel_controle"]
    atencao = contagem >= L["r5_nivel_atencao"] and praga_em_laudo_recente
    if not ((controle or atencao) and not ja_alertado):
        return None
    local = _local(leitura)
    motivo = (f"atingiu o nível de controle ({L['r5_nivel_controle']})" if controle else
              f"passou do nível de atenção ({L['r5_nivel_atencao']}) com praga confirmada em laudo "
              f"nos últimos {L['r5_janela_laudo_dias']} dias")
    return Disparo(
        regra="R5", severidade=SEV_ALTA if controle else SEV_MEDIA,
        chave_dedup=f"R5|{local['propriedade']}|{local['talhao']}|{leitura['id_leitura']}",
        titulo=f"Pragas na armadilha — {_onde(local)}",
        mensagem=f"Armadilha com {contagem} insetos: {motivo}.",
        recomendacao=("Inspecionar a face inferior das folhas, fotografar para diagnóstico e avaliar "
                      "controle biológico antes de recorrer a defensivo químico."),
        instante_evento=leitura["instante"], local=local,
        evidencias={"id_leitura": leitura["id_leitura"], "pragas_armadilha": contagem,
                    "praga_em_laudo_recente": praga_em_laudo_recente},
        acoes=(ACAO_ARQUIVO, ACAO_EMAIL) if controle else (ACAO_ARQUIVO,),
    )
