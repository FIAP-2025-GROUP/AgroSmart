"""Camada analítica da API: junta as duas fontes e responde ao dashboard.

Não importa Spark, TensorFlow nem o Gemini. Cada origem tem uma única fonte,
para que nada seja contado duas vezes:

* **simulado** → o pacote processado apontado por `dados/processados/ATUAL.json`,
  validado (manifesto, hashes, estrutura) e mantido em memória até mudar;
* **real** → o SQLite operacional, lido ao vivo por `analitica_operacional`
  (uma análise feita agora aparece no próximo carregamento, sem Spark). Laudos
  reais que estejam num pacote são ignorados, com aviso;
* **todas** → a soma das duas.

Unidades — a mesma separação do contrato da Fase 2:

* os **cards de percentual**, a evolução temporal, as localidades, as culturas,
  as categorias e os diagnósticos contam **análises** (`gold_analises`), cada
  uma com a condição do seu laudo principal;
* a distribuição por motor e a tabela de registros contam **laudos**
  (`gold_laudos`), porque cada motor emite o seu;
* **imagens distintas** contam `id_imagem` não nulos — análises sem esse campo
  são informadas à parte, não somadas como se fossem imagens novas.

Todo percentual sai com numerador e denominador. Denominador zero vira
`pct: null`, que o dashboard mostra como "sem dados", nunca como 0%.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
import re
from datetime import date
from pathlib import Path
from typing import Any

import sqlite3
from datetime import datetime, timezone

from . import analitica_operacional as operacional
from .pipeline import contratos as c
from .pipeline import pacote as pkt

NAO_INFORMADO = "__nao_informado__"
TODAS = "todas"
POR_PAGINA = 25
TOP_DIAGNOSTICOS = 10
TOP_LOCALIDADES = 12
# Abaixo disto o painel abre em "Simulados": poucas análises reais dariam
# gráficos quase vazios. A partir daqui, "Reais" vira a origem padrão.
MINIMO_REAIS_PADRAO = 20

# filtro -> (campo na análise, campo no laudo)
FILTROS: dict[str, tuple[str, str]] = {
    "uf": ("uf", "uf"),
    "municipio": ("municipio", "municipio"),
    "propriedade": ("propriedade", "propriedade"),
    "talhao": ("talhao", "talhao"),
    "cultura": ("cultura", "cultura"),
    "condicao": ("condicao", "condicao"),
    "categoria": ("categoria_problema", "categoria_problema"),
    "motor": ("motor_principal", "motor"),
}


@dataclass
class Pacote:
    pasta: Path
    manifesto: dict
    analises: list[dict]
    laudos: list[dict]
    agregados: list[dict]
    qualidade: dict
    aviso: str | None = None
    assinatura: tuple = field(default=())


_cache: dict[str, Pacote] = {}


def carregar(raiz: Path | None = None) -> Pacote | None:
    """Pacote atual validado, ou None se não houver nenhum utilizável.

    A validação (manifesto, hashes, estrutura de cada registro, contagens) e a
    leitura acontecem numa única passada em `pacote.ler`; qualquer defeito de
    arquivo vira `PacoteInvalidoError` e o pacote é simplesmente descartado.
    O resultado fica em cache até qualquer arquivo de processados mudar.
    """
    raiz = Path(raiz or c.PASTA_PROCESSADOS)
    assinatura = pkt.assinatura(raiz)
    em_cache = _cache.get(str(raiz))
    if em_cache and em_cache.assinatura == assinatura:
        return em_cache

    lido, aviso = pkt.abrir_atual(raiz)
    if lido is None:
        _cache.pop(str(raiz), None)
        return None
    pacote = Pacote(
        pasta=lido.pasta, manifesto=lido.manifesto, analises=lido.analises, laudos=lido.laudos,
        agregados=lido.agregados, qualidade=lido.qualidade, aviso=aviso, assinatura=assinatura,
    )
    _cache[str(raiz)] = pacote
    return pacote


# --------------------------------------------------------------------------- #
# Filtros
# --------------------------------------------------------------------------- #
def _bate(valor: Any, alvo: str | None) -> bool:
    if alvo in (None, ""):
        return True
    if alvo == NAO_INFORMADO:
        return valor is None
    return valor == alvo


def _filtrar(registros: list[dict], filtros: dict, indice: int, origem: str) -> list[dict]:
    inicio, fim = filtros.get("inicio"), filtros.get("fim")
    saida = []
    for r in registros:
        if origem != TODAS and r.get("origem_dado") != origem:
            continue
        dia = r.get("data_dia") or ""
        if inicio and dia < inicio:
            continue
        if fim and dia > fim:
            continue
        if all(_bate(r.get(campos[indice]), filtros.get(nome)) for nome, campos in FILTROS.items()):
            saida.append(r)
    return saida


def _contar_por_origem(analises: list[dict], laudos: list[dict]) -> dict:
    resumo = {o: {"analises": 0, "laudos": 0} for o in c.ORIGENS}
    for a in analises:
        resumo.setdefault(a["origem_dado"], {"analises": 0, "laudos": 0})["analises"] += 1
    for laudo in laudos:
        resumo.setdefault(laudo["origem_dado"], {"analises": 0, "laudos": 0})["laudos"] += 1
    return resumo


def origem_padrao(por_origem: dict) -> str:
    """Origem com que o painel abre.

    * Reais, a partir de `MINIMO_REAIS_PADRAO` análises reais identificadas;
    * senão Simulados, se houver (é o conjunto com tendências para demonstrar);
    * senão Reais, se houver qualquer coisa real;
    * senão Todos.
    Nunca mistura as duas origens por padrão.
    """
    reais = por_origem.get(c.ORIGEM_REAL, {})
    if reais.get("analises", 0) >= MINIMO_REAIS_PADRAO:
        return c.ORIGEM_REAL
    if por_origem.get(c.ORIGEM_SIMULADO, {}).get("analises"):
        return c.ORIGEM_SIMULADO
    if reais.get("analises") or reais.get("laudos"):
        return c.ORIGEM_REAL
    return TODAS


# --------------------------------------------------------------------------- #
# Indicadores
# --------------------------------------------------------------------------- #
def razao(numerador: int, denominador: int) -> dict:
    return {"num": numerador, "den": denominador,
            "pct": round(numerador / denominador, 4) if denominador else None}


def _cards(analises: list[dict], laudos: list[dict]) -> dict:
    total = len(analises)
    condicoes = Counter(a["condicao"] for a in analises)
    imagens = {a["id_imagem"] for a in analises if a.get("id_imagem")}
    sem_imagem = sum(1 for a in analises if not a.get("id_imagem"))
    return {
        "analises": total,
        "laudos": len(laudos),
        "laudos_sem_analise": sum(1 for laudo in laudos if not laudo.get("id_analise")),
        # Sem nenhuma análise com id_imagem não há o que contar: null, não zero.
        "imagens_distintas": len(imagens) if total and sem_imagem < total else None,
        "analises_sem_id_imagem": sem_imagem,
        "reanalises": sum(1 for a in analises if a.get("e_reanalise")),
        "saudavel": razao(condicoes[c.CONDICAO_SAUDAVEL], total),
        "doente": razao(condicoes[c.CONDICAO_DOENTE], total),
        "indeterminado": razao(condicoes[c.CONDICAO_INDETERMINADA], total),
        "comparacoes": sum(1 for a in analises if a.get("comparacao")),
        "comparacoes_com_veredito": sum(1 for a in analises if a.get("concordancia_motores") is not None),
        "discordancias": sum(1 for a in analises if a.get("concordancia_motores") is False),
    }


def _contagem(registros: list[dict], campo: str) -> list[dict]:
    return [{"chave": k, "n": n} for k, n in
            sorted(Counter(r.get(campo) for r in registros).items(), key=lambda kv: (-kv[1], str(kv[0])))]


def _evolucao(analises: list[dict]) -> list[dict]:
    """Série semanal com contagens — o percentual é num/den de cada semana."""
    semanas: dict[str, Counter] = defaultdict(Counter)
    for a in analises:
        semanas[a["semana"]][a["condicao"]] += 1
        semanas[a["semana"]]["total"] += 1
    saida = []
    for semana in sorted(semanas):
        cont = semanas[semana]
        saida.append({
            "semana": semana,
            "n_analises": cont["total"],
            **{f"n_{cond}": cont[cond] for cond in c.CONDICOES},
            "pct_doente": razao(cont[c.CONDICAO_DOENTE], cont["total"])["pct"],
        })
    return saida


def _localidades(analises: list[dict]) -> list[dict]:
    """Uma linha por município. Análises sem município (com ou sem UF) formam
    uma única linha de localidade incompleta, em vez de várias fatias pequenas
    por UF cujos percentuais não dizem nada."""
    grupos: dict[tuple, Counter] = defaultdict(Counter)
    for a in analises:
        chave = (a.get("uf"), a["municipio"]) if a.get("municipio") else (None, None)
        grupos[chave][a["condicao"]] += 1
        grupos[chave]["total"] += 1
    linhas = [
        {"uf": uf, "municipio": municipio, "n_analises": cont["total"],
         **{f"n_{cond}": cont[cond] for cond in c.CONDICOES},
         "doente": razao(cont[c.CONDICAO_DOENTE], cont["total"])}
        for (uf, municipio), cont in grupos.items()
    ]
    linhas.sort(key=lambda x: (-x["n_analises"], str(x["uf"]), str(x["municipio"])))
    return linhas[:TOP_LOCALIDADES]


def _culturas(analises: list[dict]) -> list[dict]:
    grupos: dict[Any, Counter] = defaultdict(Counter)
    for a in analises:
        grupos[a.get("cultura")][a["condicao"]] += 1
        grupos[a.get("cultura")]["total"] += 1
    linhas = [{"cultura": cultura, "n_analises": cont["total"],
               **{f"n_{cond}": cont[cond] for cond in c.CONDICOES}}
              for cultura, cont in grupos.items()]
    return sorted(linhas, key=lambda x: (-x["n_analises"], str(x["cultura"])))


def _diagnosticos(analises: list[dict]) -> list[dict]:
    """Problemas mais frequentes: só análises doentes (saudável não é diagnóstico de problema)."""
    doentes = [a for a in analises if a["condicao"] == c.CONDICAO_DOENTE]
    contagem = Counter((a.get("diagnostico_normalizado"), a.get("categoria_problema")) for a in doentes)
    return [{"chave": diag, "categoria": cat, "n": n, "den": len(doentes)}
            for (diag, cat), n in sorted(contagem.items(), key=lambda kv: (-kv[1], str(kv[0])))
            ][:TOP_DIAGNOSTICOS]


CAMPOS_TABELA = (
    "data_analise_ts", "origem_dado", "uf", "municipio", "propriedade", "talhao", "cultura",
    "condicao", "diagnostico_normalizado", "diagnostico_original", "categoria_problema", "motor",
    "confianca", "confianca_texto", "modo_solicitado", "id_analise", "id_laudo",
)


def _tabela(laudos: list[dict], pagina: int) -> dict:
    ordenados = sorted(laudos, key=lambda r: (r.get("data_analise_ts") or "", r.get("id_laudo") or ""),
                       reverse=True)
    paginas = max(1, -(-len(ordenados) // POR_PAGINA))
    pagina = min(max(1, pagina), paginas)
    inicio = (pagina - 1) * POR_PAGINA
    return {
        "total": len(ordenados), "pagina": pagina, "paginas": paginas, "por_pagina": POR_PAGINA,
        "itens": [{k: r.get(k) for k in CAMPOS_TABELA} for r in ordenados[inicio:inicio + POR_PAGINA]],
    }


def _opcoes(analises: list[dict], laudos: list[dict], filtros: dict) -> dict:
    """Valores possíveis de cada filtro na origem escolhida.

    A localidade é encadeada (municípios da UF escolhida, propriedades do
    município...), o resto não, para nenhum filtro "sumir" da tela.
    """
    def valores(registros, campo, **restricoes):
        vistos, tem_nulo = set(), False
        for r in registros:
            if any(v and not _bate(r.get(k), v) for k, v in restricoes.items()):
                continue
            if r.get(campo) is None:
                tem_nulo = True
            else:
                vistos.add(r[campo])
        lista = sorted(vistos, key=str)
        return lista + ([NAO_INFORMADO] if tem_nulo else [])

    base = analises + [laudo for laudo in laudos if not laudo.get("id_analise")]
    dias = [r["data_dia"] for r in base if r.get("data_dia")]
    return {
        "uf": valores(base, "uf"),
        "municipio": valores(base, "municipio", uf=filtros.get("uf")),
        "propriedade": valores(base, "propriedade", uf=filtros.get("uf"), municipio=filtros.get("municipio")),
        "talhao": valores(base, "talhao", uf=filtros.get("uf"), municipio=filtros.get("municipio"),
                          propriedade=filtros.get("propriedade")),
        "cultura": valores(base, "cultura"),
        "condicao": list(c.CONDICOES),
        "categoria": list(c.CATEGORIAS),
        "motor": list(c.MOTORES),
        "periodo": {"min": min(dias) if dias else None, "max": max(dias) if dias else None},
    }


def _qualidade(metricas: list[dict], amostra: list[dict], origem: str, analises: list[dict],
               laudos: list[dict]) -> dict:
    metricas = [m for m in metricas if origem == TODAS or m.get("origem_dado") == origem]
    resumo: dict[str, dict] = {}
    for m in metricas:
        atual = resumo.setdefault(m["metrica"], {"valor": 0, "denominador": 0, "unidade": m.get("unidade")})
        atual["valor"] += m.get("valor") or 0
        atual["denominador"] += m.get("denominador") or 0
    for chave_m, item in resumo.items():
        item["pct"] = razao(item["valor"], item["denominador"])["pct"]

    total = len(analises)
    return {
        "pacote": resumo,
        "rejeitados_amostra": [r for r in amostra
                               if origem == TODAS or r.get("origem_dado") == origem][:10],
        "recorte": {
            "localidade_completa": razao(sum(1 for a in analises if a.get("uf") and a.get("municipio")), total),
            "com_id_imagem": razao(sum(1 for a in analises if a.get("id_imagem")), total),
            "laudos_sem_analise": razao(sum(1 for laudo in laudos if not laudo.get("id_analise")), len(laudos)),
        },
    }


_PADRAO_DATA = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_ENUMERADOS = {
    "condicao": c.CONDICOES,
    "categoria": c.CATEGORIAS,
    "motor": c.MOTORES,
}


def validar_filtros(filtros: dict | None) -> dict:
    """Filtros normalizados, ou `ValueError` com mensagem para o usuário.

    * datas só no formato público AAAA-MM-DD (o `date.fromisoformat` do Python
      também aceitaria `20260803`, que depois compararia errado como texto);
    * `inicio` não pode ser posterior a `fim` — intervalo invertido é erro, não
      um recorte silenciosamente vazio;
    * origem, condição, categoria e motor só aceitam os valores do contrato.
    Campos livres (UF, município, propriedade, talhão, cultura) aceitam qualquer
    texto, inclusive `__nao_informado__`.
    """
    limpos = {k: v.strip() if isinstance(v, str) else v
              for k, v in (filtros or {}).items() if v not in (None, "")}
    for nome in ("inicio", "fim"):
        if nome in limpos:
            valor = limpos[nome]
            if not isinstance(valor, str) or not _PADRAO_DATA.match(valor):
                raise ValueError(f"'{nome}' deve ser uma data no formato AAAA-MM-DD.")
            try:
                limpos[nome] = date.fromisoformat(valor).isoformat()
            except ValueError:
                raise ValueError(f"'{nome}' não é uma data válida: {valor}.") from None
    if "inicio" in limpos and "fim" in limpos and limpos["inicio"] > limpos["fim"]:
        raise ValueError("Intervalo invertido: 'inicio' é posterior a 'fim'.")
    if "origem" in limpos and limpos["origem"] not in (*c.ORIGENS, TODAS):
        raise ValueError(f"Origem desconhecida: {limpos['origem']}. Use real, simulado ou todas.")
    for nome, permitidos in _ENUMERADOS.items():
        if nome in limpos and limpos[nome] not in permitidos:
            raise ValueError(f"Valor inválido para '{nome}': {limpos[nome]}. "
                             f"Aceitos: {', '.join(permitidos)}.")
    return limpos


def consultar(filtros: dict | None = None, pagina: int = 1, raiz: Path | None = None) -> dict:
    """Tudo o que o dashboard precisa, numa resposta só."""
    filtros = validar_filtros(filtros)
    if not isinstance(pagina, int) or pagina < 1:
        raise ValueError("'pagina' deve ser um inteiro maior ou igual a 1.")

    pacote = carregar(raiz)
    aviso_real = None
    try:
        vivo = operacional.carregar()
    except (sqlite3.Error, OSError) as erro:  # banco travado/corrompido não derruba o painel
        vivo = operacional.Operacional()
        aviso_real = f"Não foi possível ler o histórico local agora: {erro}"

    if pacote is None and not vivo.laudos:
        _, aviso = pkt.abrir_atual(raiz)
        return {
            "disponivel": False,
            "mensagem": ("Nenhum dado disponível: não há análises reais no histórico e nenhum pacote "
                         "processado válido. Analise uma imagem na tela principal, rode o pipeline "
                         "(ferramentas/executar_pipeline.py) ou importe um pacote do Databricks "
                         "(ferramentas/importar_processados.py)."),
            "aviso": aviso or aviso_real,
        }

    # Simulados só do pacote; reais só da fonte operacional.
    nao_real = (lambda r: r.get("origem_dado") != c.ORIGEM_REAL)
    todas_analises = [a for a in (pacote.analises if pacote else []) if nao_real(a)] + vivo.analises
    todos_laudos = [x for x in (pacote.laudos if pacote else []) if nao_real(x)] + vivo.laudos
    metricas = [m for m in (pacote.qualidade.get("metricas", []) if pacote else []) if nao_real(m)]
    metricas += vivo.metricas
    amostra = [r for r in (pacote.qualidade.get("amostra_rejeitados", []) if pacote else []) if nao_real(r)]
    reais_no_pacote = sum(1 for x in (pacote.laudos if pacote else []) if not nao_real(x))

    avisos = [aviso for aviso in (pacote.aviso if pacote else None, aviso_real) if aviso]
    if reais_no_pacote:
        avisos.append(f"O pacote processado contém {reais_no_pacote} laudo(s) reais. A visão Reais usa o "
                      "histórico local ao vivo, então eles foram ignorados para não contar duas vezes.")

    por_origem = _contar_por_origem(todas_analises, todos_laudos)
    padrao = origem_padrao(por_origem)
    origem = filtros.pop("origem", None) or padrao

    analises = _filtrar(todas_analises, filtros, 0, origem)
    laudos = _filtrar(todos_laudos, filtros, 1, origem)
    analises_origem = _filtrar(todas_analises, {}, 0, origem)
    laudos_origem = _filtrar(todos_laudos, {}, 1, origem)

    manifesto = pacote.manifesto if pacote else {}
    return {
        "disponivel": True,
        "pacote": {
            "id_execucao": manifesto.get("id_execucao"),
            "processado_em": manifesto.get("processado_em"),
            "publicado_em": manifesto.get("publicado_em"),
            "ambiente": manifesto.get("ambiente"),
            "id_snapshot": manifesto.get("id_snapshot"),
            "snapshot_extraido_em": manifesto.get("snapshot_extraido_em"),
            "aviso": " ".join(avisos) or None,
        },
        "fonte_real": {
            "tipo": "sqlite_operacional",
            "lida_em": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "analises": len(vivo.analises),
            "laudos": len(vivo.laudos),
        },
        "minimo_reais_padrao": MINIMO_REAIS_PADRAO,
        "origens": por_origem,
        "origem_padrao": padrao,
        "origem": origem,
        "filtros": filtros,
        "opcoes": _opcoes(analises_origem, laudos_origem, filtros),
        "cards": _cards(analises, laudos),
        "graficos": {
            "condicao": _contagem(analises, "condicao"),
            "categorias": _contagem(analises, "categoria_problema"),
            "diagnosticos": _diagnosticos(analises),
            "evolucao": _evolucao(analises),
            "localidades": _localidades(analises),
            "culturas": _culturas(analises),
            "motores_laudos": _contagem(laudos, "motor"),
            "motores_analises": _contagem(analises, "motor_principal"),
        },
        "tabela": _tabela(laudos, pagina),
        "qualidade": _qualidade(metricas, amostra, origem, analises, laudos),
    }
