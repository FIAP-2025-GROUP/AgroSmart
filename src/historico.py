"""Histórico persistente dos laudos, em SQLite.

Guarda o laudo completo e uma miniatura da imagem analisada, para que o
usuário possa reabrir uma análise antiga sem depender do arquivo original — e
para que a exportação cubra tudo o que já foi diagnosticado, não só o lote
atual. É o "banco de imagens" pedido no enunciado da atividade.
"""

from __future__ import annotations

import io
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps

from .tipos import Predicao, Resultado

RAIZ = Path(__file__).resolve().parent.parent
CAMINHO_BANCO = RAIZ / "dados" / "historico.db"

LADO_MINIATURA = 480

ESQUEMA = """
CREATE TABLE IF NOT EXISTS laudos (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    arquivo         TEXT    NOT NULL,
    data_hora       TEXT    NOT NULL,
    motor           TEXT    NOT NULL,
    especie         TEXT,
    diagnostico     TEXT,
    condicao        TEXT,
    confianca       REAL,
    confianca_texto TEXT,
    agente          TEXT,
    sintomas        TEXT,
    manejo          TEXT,
    observacoes     TEXT,
    modelo_versao   TEXT,
    miniatura       BLOB
);
CREATE INDEX IF NOT EXISTS idx_laudos_data ON laudos (data_hora DESC);
"""


def _conectar() -> sqlite3.Connection:
    CAMINHO_BANCO.parent.mkdir(parents=True, exist_ok=True)
    conexao = sqlite3.connect(CAMINHO_BANCO)
    conexao.row_factory = sqlite3.Row
    conexao.executescript(ESQUEMA)
    return conexao


def _miniatura(imagem: Image.Image) -> bytes:
    """JPEG reduzido da imagem original, para o cartão do histórico."""
    copia = ImageOps.exif_transpose(imagem).convert("RGB")
    copia.thumbnail((LADO_MINIATURA, LADO_MINIATURA), Image.LANCZOS)
    buffer = io.BytesIO()
    copia.save(buffer, format="JPEG", quality=82)
    return buffer.getvalue()


def salvar(resultado: Resultado, imagem: Image.Image) -> int:
    """Grava um laudo e devolve o id gerado."""
    with _conectar() as conexao:
        cursor = conexao.execute(
            """
            INSERT INTO laudos (
                arquivo, data_hora, motor, especie, diagnostico, condicao,
                confianca, confianca_texto, agente, sintomas, manejo,
                observacoes, modelo_versao, miniatura
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                resultado.arquivo,
                resultado.data_hora,
                resultado.motor,
                resultado.especie,
                resultado.diagnostico,
                resultado.condicao,
                resultado.confianca,
                resultado.confianca_texto,
                resultado.agente,
                resultado.sintomas,
                resultado.manejo,
                resultado.observacoes,
                resultado.modelo_versao,
                _miniatura(imagem),
            ),
        )
        return int(cursor.lastrowid)


def _para_dicionario(linha: sqlite3.Row) -> dict[str, Any]:
    """Linha do banco no formato que o frontend consome (sem o blob)."""
    dados = {chave: linha[chave] for chave in linha.keys() if chave != "miniatura"}
    dados["data_legivel"] = _formatar_data(dados["data_hora"])
    return dados


def _formatar_data(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso).strftime("%d/%m/%Y às %H:%M")
    except ValueError:
        return iso


def listar(limite: int = 60) -> list[dict[str, Any]]:
    """Laudos mais recentes primeiro."""
    with _conectar() as conexao:
        linhas = conexao.execute(
            "SELECT * FROM laudos ORDER BY id DESC LIMIT ?", (limite,)
        ).fetchall()
    return [_para_dicionario(linha) for linha in linhas]


def obter(id_laudo: int) -> dict[str, Any] | None:
    with _conectar() as conexao:
        linha = conexao.execute("SELECT * FROM laudos WHERE id = ?", (id_laudo,)).fetchone()
    return _para_dicionario(linha) if linha else None


def miniatura(id_laudo: int) -> bytes | None:
    with _conectar() as conexao:
        linha = conexao.execute(
            "SELECT miniatura FROM laudos WHERE id = ?", (id_laudo,)
        ).fetchone()
    return linha["miniatura"] if linha else None


def remover(id_laudo: int) -> bool:
    with _conectar() as conexao:
        cursor = conexao.execute("DELETE FROM laudos WHERE id = ?", (id_laudo,))
        return cursor.rowcount > 0


def limpar() -> int:
    """Apaga todo o histórico. Devolve quantos laudos foram removidos."""
    with _conectar() as conexao:
        cursor = conexao.execute("DELETE FROM laudos")
        return cursor.rowcount


def total() -> int:
    with _conectar() as conexao:
        return int(conexao.execute("SELECT COUNT(*) FROM laudos").fetchone()[0])


def como_resultados() -> list[Resultado]:
    """Todo o histórico no tipo `Resultado`, para alimentar a exportação.

    O ranking do CNN não é persistido — as colunas de alternativas saem vazias
    no CSV do histórico, o que é aceitável: quem quer o ranking completo exporta
    na hora da análise.
    """
    with _conectar() as conexao:
        linhas = conexao.execute("SELECT * FROM laudos ORDER BY id").fetchall()

    return [
        Resultado(
            arquivo=linha["arquivo"],
            data_hora=linha["data_hora"],
            motor=linha["motor"],
            especie=linha["especie"] or "—",
            diagnostico=linha["diagnostico"] or "—",
            condicao=linha["condicao"] or "",
            confianca=linha["confianca"],
            confianca_texto=linha["confianca_texto"] or "—",
            agente=linha["agente"] or "—",
            sintomas=linha["sintomas"] or "",
            manejo=linha["manejo"] or "",
            modelo_versao=linha["modelo_versao"] or "—",
            observacoes=linha["observacoes"] or "",
            ranking=(),
        )
        for linha in linhas
    ]


__all__ = [
    "salvar",
    "listar",
    "obter",
    "miniatura",
    "remover",
    "limpar",
    "total",
    "como_resultados",
    "Predicao",
]
