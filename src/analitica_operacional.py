"""Fonte operacional ao vivo: análises reais lidas direto do SQLite da Fase 1.

Por que existe: uma análise feita agora pela interface precisa aparecer no
painel sem rodar Spark. O Spark/Databricks continua sendo o pipeline em lote
(SQLite/simulados → RAW → Bronze → Silver → Gold → pacote); este módulo só
aplica as mesmas regras, em Python puro, aos laudos **reais** do banco local.

Divisão de fontes no painel (evita dupla contagem):

* origem **real** → sempre daqui, ao vivo (é a fonte da verdade: mostra o envio
  recém-feito e respeita exclusões);
* origem **simulado** → sempre do pacote processado;
* registros reais que estejam num pacote são ignorados pela API.

Regras espelhadas de `transformacoes_spark.py` (um teste de paridade roda o
mesmo histórico pelos dois caminhos e compara):

* normalização por `normalizacao.py` (as mesmas tabelas e funções de
  referência);
* validação: data legível, origem, motor e condição conhecidos, confiança em
  [0, 1] e nula no VLM — o que falhar é contado como rejeitado;
* laudo principal: CNN conclusivo, senão VLM conclusivo, senão CNN, senão VLM;
* reanálise: a mesma imagem (`origem_dado + id_imagem`) já analisada antes;
* datas sem fuso são horário de Brasília (UTC−3, sem horário de verão desde
  2019), como o `spark.sql.session.timeZone` do pipeline.

O banco é aberto em modo somente leitura (`ingestao.ler_historico_sqlite`) e
o resultado fica em cache até o arquivo mudar.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import historico
from .pipeline import contratos as c
from .pipeline import ingestao
from .pipeline import normalizacao as n

FUSO_BRASILIA = timezone(timedelta(hours=-3))
CAMPOS_TEXTO = [nome for nome, tipo in c.CAMPOS_LAUDO if tipo == "string"]


@dataclass
class Operacional:
    analises: list[dict] = field(default_factory=list)
    laudos: list[dict] = field(default_factory=list)
    metricas: list[dict] = field(default_factory=list)
    assinatura: tuple = ()


_cache: dict[str, Operacional] = {}


def _data(texto: str | None) -> datetime | None:
    if not texto:
        return None
    try:
        instante = datetime.fromisoformat(texto)
    except ValueError:
        return None
    if instante.tzinfo is None:
        instante = instante.replace(tzinfo=FUSO_BRASILIA)
    return instante.astimezone(FUSO_BRASILIA)


def _laudo_silver(raw: dict) -> tuple[dict | None, list[str]]:
    """(laudo normalizado, motivos de rejeição)."""
    r = dict(raw)
    for campo in CAMPOS_TEXTO:
        r[campo] = n.limpar(r.get(campo))
    instante = _data(r.get("data_analise"))
    condicao = n.normalizar_condicao(r.get("condicao"))
    motor = n.normalizar_motor(r.get("motor"))
    confianca = r.get("confianca")

    motivos = []
    if not r.get("id_laudo"):
        motivos.append("id_laudo_ausente")
    if instante is None:
        motivos.append("data_invalida" if r.get("data_analise") else "data_ausente")
    if r.get("origem_dado") != c.ORIGEM_REAL:
        motivos.append("origem_dado_invalida")
    if motor is None:
        motivos.append("motor_desconhecido")
    if condicao is None:
        motivos.append("condicao_invalida")
    if confianca is not None and not 0 <= confianca <= 1:
        motivos.append("confianca_fora_do_intervalo")
    if motor == c.MOTOR_VLM and confianca is not None:
        motivos.append("confianca_numerica_em_laudo_vlm")
    if motivos:
        return None, motivos

    cultura, cultura_mapeada = n.normalizar_cultura(r.get("cultura"), r.get("especie_original"))
    diagnostico, categoria, mapeado = n.normalizar_diagnostico(
        condicao, r.get("diagnostico_original"), r.get("agente_original"))
    dia = instante.date()
    r.update(
        condicao_original=r.get("condicao"), motor_original=r.get("motor"),
        condicao=condicao, motor=motor, cultura=cultura, cultura_mapeada=cultura_mapeada,
        diagnostico_normalizado=diagnostico, categoria_problema=categoria,
        diagnostico_mapeado=mapeado if condicao == c.CONDICAO_DOENTE else True,
        tipo_amostra=r.get("tipo_amostra") if r.get("tipo_amostra") in c.TIPOS_AMOSTRA else None,
        data_analise_ts=instante.isoformat(timespec="seconds"),
        data_dia=dia.isoformat(),
        semana=(dia - timedelta(days=dia.weekday())).isoformat(),
    )
    return r, []


def _prioridade(laudo: dict) -> int:
    conclusivo = laudo["condicao"] != c.CONDICAO_INDETERMINADA
    if laudo["motor"] == c.MOTOR_CNN:
        return 0 if conclusivo else 2
    return 1 if conclusivo else 3


def _analises(laudos: list[dict]) -> list[dict]:
    grupos: dict[tuple, list[dict]] = defaultdict(list)
    for laudo in laudos:
        if laudo.get("id_analise"):
            grupos[(laudo["origem_dado"], laudo["origem_instancia"], laudo["id_analise"])].append(laudo)

    analises = []
    for grupo in grupos.values():
        principal = min(grupo, key=lambda x: (_prioridade(x), x["data_analise_ts"], x["id_laudo"]))
        cnn = [x for x in grupo if x["motor"] == c.MOTOR_CNN]
        vlm = [x for x in grupo if x["motor"] == c.MOTOR_VLM]
        condicao_cnn = min((x["condicao"] for x in cnn), default=None)
        condicao_vlm = min((x["condicao"] for x in vlm), default=None)
        concordam = None
        if condicao_cnn and condicao_vlm and c.CONDICAO_INDETERMINADA not in (condicao_cnn, condicao_vlm):
            concordam = condicao_cnn == condicao_vlm
        inicio = min(x["data_analise_ts"] for x in grupo)
        dia = datetime.fromisoformat(inicio).date()
        culturas = [x["cultura"] for x in grupo if x.get("cultura")]
        analises.append({
            **{k: principal.get(k) for k in (
                "id_analise", "origem_dado", "origem_instancia", "id_imagem", "arquivo", "fuso_origem",
                "uf", "municipio", "propriedade", "talhao", "modo_solicitado", "limiar_cnn",
                "tipo_amostra", "condicao", "diagnostico_normalizado", "categoria_problema")},
            "cultura": principal.get("cultura") or (min(culturas) if culturas else None),
            "data_analise_ts": inicio,
            "data_dia": dia.isoformat(),
            "semana": (dia - timedelta(days=dia.weekday())).isoformat(),
            "motor_principal": principal["motor"],
            "id_laudo_principal": principal["id_laudo"],
            "n_laudos": len(grupo),
            "tem_cnn": bool(cnn), "tem_vlm": bool(vlm), "comparacao": bool(cnn and vlm),
            "condicao_cnn": condicao_cnn, "condicao_vlm": condicao_vlm,
            "concordancia_motores": concordam,
            "confianca_cnn": max((x["confianca"] for x in cnn if x["confianca"] is not None), default=None),
        })

    # Reanálise: a mesma imagem já tinha sido analisada antes (desconhecido sem id_imagem).
    vistas: set[tuple] = set()
    for analise in sorted(analises, key=lambda a: (a["data_analise_ts"], a["origem_instancia"] or "",
                                                   a["id_analise"])):
        if not analise["id_imagem"]:
            analise["e_reanalise"] = None
            continue
        chave = (analise["origem_dado"], analise["id_imagem"])
        analise["e_reanalise"] = chave in vistas
        vistas.add(chave)
    return sorted(analises, key=lambda a: (a["data_analise_ts"], a["id_analise"]))


def _metricas(recebidos: int, n_rejeitados: int, rejeitados: dict[str, int], laudos: list[dict],
              analises: list[dict]) -> list[dict]:
    """Mesmos nomes de `gold_qualidade`, só para a origem real."""
    def m(metrica, valor, den, unidade):
        return {"origem_dado": c.ORIGEM_REAL, "metrica": metrica, "valor": valor,
                "denominador": den, "unidade": unidade}

    if not recebidos:
        return []
    saida = [m("registros_recebidos", recebidos, recebidos, "registro_raw"),
             m("rejeitados", n_rejeitados, recebidos, "registro_raw")]
    saida += [m(f"rejeitado:{motivo}", v, recebidos, "registro_raw") for motivo, v in sorted(rejeitados.items())]
    nl = len(laudos)
    saida += [
        m("laudos_validos", nl, nl, "laudo"),
        m("laudo_sem_analise", sum(1 for x in laudos if not x.get("id_analise")), nl, "laudo"),
        m("identidade_incompleta", sum(1 for x in laudos if not x.get("id_analise") or not x.get("id_imagem")),
          nl, "laudo"),
        m("localidade_ausente", sum(1 for x in laudos if not x.get("uf") or not x.get("municipio")), nl, "laudo"),
        m("diagnostico_nao_mapeado", sum(1 for x in laudos if x["condicao"] == c.CONDICAO_DOENTE
                                         and not x["diagnostico_mapeado"]), nl, "laudo"),
    ]
    na = len(analises)
    if na:
        saida += [
            m("analises_identificadas", na, na, "analise"),
            m("reanalises", sum(1 for a in analises if a["e_reanalise"]), na, "analise"),
            m("comparacoes", sum(1 for a in analises if a["comparacao"]), na, "analise"),
        ]
    return saida


def carregar(caminho: Path | None = None) -> Operacional:
    """Análises, laudos e métricas reais, ao vivo. Banco ausente → tudo vazio."""
    caminho = Path(caminho or historico.CAMINHO_BANCO)
    try:
        estado = caminho.stat()
        assinatura = (str(caminho), estado.st_mtime_ns, estado.st_size)
    except OSError:
        return Operacional()
    em_cache = _cache.get(str(caminho))
    if em_cache and em_cache.assinatura == assinatura:
        return em_cache

    brutos = ingestao.ler_historico_sqlite(caminho)
    laudos, rejeitados, n_rejeitados = [], defaultdict(int), 0
    for bruto in brutos:
        laudo, motivos = _laudo_silver(bruto)
        if laudo is None:
            n_rejeitados += 1
            for motivo in motivos:
                rejeitados[motivo] += 1
        else:
            laudos.append(laudo)
    analises = _analises(laudos)
    resultado = Operacional(analises=analises, laudos=laudos,
                            metricas=_metricas(len(brutos), n_rejeitados, rejeitados, laudos, analises),
                            assinatura=assinatura)
    _cache[str(caminho)] = resultado
    return resultado
