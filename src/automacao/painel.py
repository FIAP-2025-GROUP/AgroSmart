"""Dados da página `/automacao`: estado do monitor, alertas, ações e irrigação.

Só **lê** o banco da Fase 3 (conexão `mode=ro`): o painel nunca altera estado
nem dispara ação. Banco ausente → resposta com `disponivel: false`.
"""

from __future__ import annotations

import json
from datetime import datetime
from email import policy
from email.parser import BytesParser
from pathlib import Path

from . import armazenamento as db
from . import regras
from .contratos import LIMITES, VERSAO_REGRAS, Ambiente
from .ingestao import FUSO_BRASILIA


def _pulso(ambiente: Ambiente) -> dict:
    try:
        pulso = json.loads((ambiente.base / "pulso.json").read_text(encoding="utf-8"))
        quando = datetime.fromisoformat(pulso["ultima_varredura"])
    except (OSError, ValueError, KeyError, TypeError):
        return {"ativo": False, "ultima_varredura": None, "segundos": None, "intervalo_s": None}
    segundos = (datetime.now(FUSO_BRASILIA) - quando).total_seconds()
    intervalo = pulso.get("intervalo_s")
    ativo = intervalo is not None and segundos <= max(3 * intervalo, 15)
    return {"ativo": ativo, "ultima_varredura": pulso["ultima_varredura"], "segundos": round(segundos),
            "intervalo_s": intervalo}


def _exibir(caminho: Path) -> str:
    """Caminho relativo ao projeto quando possível (não expõe a pasta do usuário na tela)."""
    from .contratos import RAIZ
    try:
        return caminho.resolve().relative_to(RAIZ.resolve()).as_posix()
    except ValueError:
        return caminho.name


def _recomendacoes(alertas: list[dict]) -> list[dict]:
    """Uma recomendação por talhão.

    Vale o alerta mais recente que pede ação, considerando só o último alerta de cada regra: um R3
    "irrigação desligada" encerra o R3 anterior, mas não esconde um R1/R2 ainda vigente no mesmo talhão.
    Sem nada vigente, mostra o último informativo ("situação normalizada").
    """
    por_talhao: dict[tuple, list[dict]] = {}
    for alerta in alertas:  # já em ordem decrescente de tempo do dado
        por_talhao.setdefault((alerta["propriedade"], alerta["talhao"]), []).append(alerta)
    saida = []
    for lista in por_talhao.values():
        ultimos, vistos = [], set()
        for alerta in lista:
            if alerta["regra"] not in vistos:
                vistos.add(alerta["regra"])
                ultimos.append(alerta)
        vigente = next((a for a in ultimos if a["severidade"] != "informativa"), None)
        saida.append(vigente or ultimos[0])
    return sorted(saida, key=lambda a: a["instante_evento"], reverse=True)


def consultar(ambiente: Ambiente | None = None, limite: int = 60) -> dict:
    ambiente = ambiente or Ambiente.padrao()
    base = {
        "versao_regras": VERSAO_REGRAS,
        "entrada": _exibir(ambiente.entrada),
        "regras": [vars(r) for r in regras.CATALOGO],
        "limites": {k: list(v) if isinstance(v, tuple) else v for k, v in LIMITES.items()},
        "monitor": _pulso(ambiente),
    }
    conexao = db.conectar_leitura(ambiente.banco)
    if conexao is None:
        return {**base, "disponivel": False,
                "motivo": "O monitor ainda não rodou. Inicie ferramentas/monitor_fase3.py."}
    try:
        def um(sql, *args):
            return conexao.execute(sql, args).fetchone()[0]

        totais = {
            "arquivos": um("SELECT COUNT(*) FROM arquivos"),
            "lotes": um("SELECT COUNT(*) FROM lotes"),
            "laudos": um("SELECT COUNT(*) FROM laudos"),
            "leituras": um("SELECT COUNT(*) FROM leituras"),
            "duplicados": um("SELECT COALESCE(SUM(duplicados), 0) FROM lotes"),
            "rejeitados": um("SELECT COUNT(*) FROM rejeitados"),
            "alertas": um("SELECT COUNT(*) FROM alertas"),
            "acoes_executadas": um("SELECT COUNT(*) FROM acoes WHERE status = 'executada'"),
            "acoes_pendentes": um("SELECT COUNT(*) FROM acoes WHERE status = 'pendente'"),
            "acoes_falhas": um("SELECT COUNT(*) FROM acoes WHERE status = 'falhou'"),
            "emails": um("SELECT COUNT(*) FROM acoes WHERE tipo = 'email' AND status = 'executada'"),
            "relatorios": um("SELECT COUNT(*) FROM acoes WHERE tipo = 'relatorio' AND status = 'executada'"),
        }
        por_regra = {r["regra"]: r["n"] for r in conexao.execute(
            "SELECT regra, COUNT(*) AS n FROM alertas GROUP BY regra")}

        alertas = db.linhas(conexao.execute(
            "SELECT * FROM alertas ORDER BY id DESC LIMIT ?", (limite,)))
        ids = [a["id"] for a in alertas]
        acoes = db.linhas(conexao.execute(
            f"SELECT id, id_alerta, tipo, status, destino, detalhe, executada_em FROM acoes "
            f"WHERE id_alerta IN ({','.join('?' for _ in ids) or 'NULL'})", ids))
        for alerta in alertas:
            alerta["evidencias"] = json.loads(alerta["evidencias"])
            alerta["acoes"] = sorted((x for x in acoes if x["id_alerta"] == alerta["id"]),
                                     key=lambda x: regras.ORDEM_ACOES.index(x["tipo"]))

        recomendacoes = _recomendacoes(db.linhas(conexao.execute(
            "SELECT * FROM alertas ORDER BY instante_evento DESC, id DESC")))

        talhoes = db.linhas(conexao.execute(
            """SELECT l.propriedade, l.talhao, l.cultura, l.municipio, l.uf, l.umidade_solo_pct,
                      l.temperatura_c, l.umidade_ar_pct, l.chuva_mm, l.pragas_armadilha, l.instante
               FROM leituras l JOIN (SELECT propriedade, talhao, MAX(instante) AS ultimo FROM leituras
                                     GROUP BY propriedade, talhao) u
                 ON l.propriedade = u.propriedade AND l.talhao = u.talhao AND l.instante = u.ultimo
               ORDER BY l.propriedade, l.talhao"""))
        irrigacao = {(r["propriedade"], r["talhao"]): r for r in db.linhas(conexao.execute("SELECT * FROM irrigacao"))}
        for t in talhoes:
            estado = irrigacao.get((t["propriedade"], t["talhao"]))
            t["irrigacao_ligada"] = bool(estado["ligada"]) if estado else False
            t["irrigacao_desde"] = estado["desde"] if estado else None

        return {
            **base, "disponivel": True, "totais": totais, "por_regra": por_regra,
            "talhoes": talhoes, "recomendacoes": recomendacoes, "alertas": alertas,
            "lotes": db.linhas(conexao.execute("SELECT * FROM lotes ORDER BY id DESC LIMIT 25")),
            "arquivos": db.linhas(conexao.execute(
                "SELECT nome, tipo, bytes_lidos, linhas_lidas, primeira_leitura, ultima_leitura "
                "FROM arquivos ORDER BY ultima_leitura DESC, nome LIMIT 40")),
            "rejeitados": db.linhas(conexao.execute(
                "SELECT arquivo, linha, motivos FROM rejeitados ORDER BY id DESC LIMIT 20")),
        }
    finally:
        conexao.close()


def conteudo_da_acao(id_acao: int, ambiente: Ambiente | None = None) -> dict | None:
    """Texto do arquivo gerado por uma ação (e-mail decodificado), ou None."""
    ambiente = ambiente or Ambiente.padrao()
    conexao = db.conectar_leitura(ambiente.banco)
    if conexao is None:
        return None
    try:
        linha = conexao.execute("SELECT tipo, destino FROM acoes WHERE id = ? AND status = 'executada'",
                                (id_acao,)).fetchone()
    finally:
        conexao.close()
    if linha is None or not linha["destino"]:
        return None
    caminho = (ambiente.base / linha["destino"]).resolve()
    if ambiente.saida.resolve() not in caminho.parents or not caminho.is_file():
        return None  # só arquivos gerados dentro de saida/
    if caminho.suffix == ".eml":
        mensagem = BytesParser(policy=policy.default).parsebytes(caminho.read_bytes())
        corpo = mensagem.get_body(preferencelist=("plain",))
        anexos = [p.get_filename() for p in mensagem.iter_attachments()]
        texto = "\n".join(f"{k}: {mensagem[k]}" for k in ("From", "To", "Subject", "Date"))
        texto += f"\nAnexos: {', '.join(anexos) or '—'}\n\n{corpo.get_content() if corpo else ''}"
    else:
        texto = caminho.read_text(encoding="utf-8")
    return {"tipo": linha["tipo"], "arquivo": Path(linha["destino"]).as_posix(), "texto": texto}
