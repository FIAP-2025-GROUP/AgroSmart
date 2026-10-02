"""Histórico persistente dos laudos, em SQLite.

Guarda o laudo completo e uma miniatura da imagem analisada, para que o
usuário possa reabrir uma análise antiga sem depender do arquivo original — e
para que a exportação cubra tudo o que já foi diagnosticado, não só o lote
atual. É o "banco de imagens" pedido no enunciado da atividade.

Fase 2 — tabelas complementares (a tabela `laudos` não muda):

* `analises`: uma linha por envio feito pela interface principal, com
  `id_analise` (UUID), `id_imagem` (SHA-256 dos bytes recebidos), modo, limiar
  efetivamente aplicado e localidade opcional;
* `analise_laudos`: liga a análise aos laudos que ela produziu. No modo
  comparação: 1 análise, 1 imagem, 2 laudos.

Laudos gravados antes disso (ou por `salvar()`, usado pelo app Streamlit) não
têm vínculo e continuam valendo como estão.
"""

from __future__ import annotations

import hashlib
import io
import sqlite3
import uuid
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

CREATE TABLE IF NOT EXISTS analises (
    id_analise  TEXT PRIMARY KEY,
    id_imagem   TEXT NOT NULL,
    arquivo     TEXT NOT NULL,
    data_hora   TEXT NOT NULL,
    modo        TEXT NOT NULL,
    limiar      REAL,
    uf          TEXT,
    municipio   TEXT,
    propriedade TEXT,
    talhao      TEXT
);
CREATE TABLE IF NOT EXISTS analise_laudos (
    id_analise TEXT    NOT NULL REFERENCES analises (id_analise),
    id_laudo   INTEGER NOT NULL UNIQUE REFERENCES laudos (id),
    PRIMARY KEY (id_analise, id_laudo)
);
"""

UFS = frozenset(
    "AC AL AP AM BA CE DF ES GO MA MT MS MG PA PB PR PE PI RJ RN RS RO RR SC SP SE TO".split()
)
TAMANHO_MAXIMO_LOCAL = 80
CAMPOS_LOCAL = ("uf", "municipio", "propriedade", "talhao")


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


def _inserir_laudo(conexao: sqlite3.Connection, resultado: Resultado, miniatura: bytes) -> int:
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
            miniatura,
        ),
    )
    return int(cursor.lastrowid)


def salvar(resultado: Resultado, imagem: Image.Image) -> int:
    """Grava um laudo e devolve o id gerado (sem vínculo de análise)."""
    with _conectar() as conexao:
        return _inserir_laudo(conexao, resultado, _miniatura(imagem))


def normalizar_contexto(contexto: dict[str, Any] | None) -> dict[str, str | None]:
    """Localidade opcional: texto aparado, vazio vira None, UF entre as 27 siglas.

    Levanta `ValueError` com mensagem para o usuário.
    """
    saida: dict[str, str | None] = {}
    for campo in CAMPOS_LOCAL:
        valor = (contexto or {}).get(campo)
        valor = " ".join(str(valor).split()) if valor is not None else ""
        if len(valor) > TAMANHO_MAXIMO_LOCAL:
            raise ValueError(f"'{campo}' aceita no máximo {TAMANHO_MAXIMO_LOCAL} caracteres.")
        saida[campo] = valor or None
    if saida["uf"] is not None:
        saida["uf"] = saida["uf"].upper()
        if saida["uf"] not in UFS:
            raise ValueError(f"UF inválida: {saida['uf']}. Use a sigla, por exemplo SP.")
    return saida


def id_da_imagem(conteudo: bytes) -> str:
    """Identidade da imagem: SHA-256 dos bytes exatamente como recebidos.

    O mesmo arquivo enviado de novo é a mesma imagem (reanálise); uma cópia
    recomprimida ou recortada é outra imagem.
    """
    return hashlib.sha256(conteudo).hexdigest()


def salvar_analise(
    resultados: list[Resultado],
    imagem: Image.Image,
    conteudo: bytes,
    arquivo: str,
    modo: str,
    limiar: float | None,
    contexto: dict[str, Any] | None = None,
) -> tuple[str, list[int]]:
    """Grava um envio completo numa única transação: os laudos (como `salvar`),
    a análise e os vínculos. Devolve (id_analise, ids dos laudos).

    `limiar` é o limiar efetivamente aplicado — None quando o CNN não rodou.
    Se qualquer parte falhar, nada é gravado.
    """
    if not resultados:
        raise ValueError("Uma análise precisa de ao menos um laudo.")
    local = normalizar_contexto(contexto)
    id_analise = str(uuid.uuid4())
    miniatura = _miniatura(imagem)
    conexao = _conectar()
    try:
        with conexao:  # commit no fim; rollback em qualquer exceção
            ids = [_inserir_laudo(conexao, resultado, miniatura) for resultado in resultados]
            conexao.execute(
                """
                INSERT INTO analises (id_analise, id_imagem, arquivo, data_hora, modo, limiar,
                                      uf, municipio, propriedade, talhao)
                VALUES (?,?,?,?,?,?,?,?,?,?)
                """,
                (id_analise, id_da_imagem(conteudo), arquivo, min(r.data_hora for r in resultados),
                 modo, limiar, local["uf"], local["municipio"], local["propriedade"], local["talhao"]),
            )
            conexao.executemany(
                "INSERT INTO analise_laudos (id_analise, id_laudo) VALUES (?, ?)",
                [(id_analise, id_laudo) for id_laudo in ids],
            )
    finally:
        conexao.close()
    return id_analise, ids


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
    """Apaga o laudo e o seu vínculo; a análise que ficar sem laudos sai junto."""
    with _conectar() as conexao:
        cursor = conexao.execute("DELETE FROM laudos WHERE id = ?", (id_laudo,))
        conexao.execute("DELETE FROM analise_laudos WHERE id_laudo = ?", (id_laudo,))
        conexao.execute(
            "DELETE FROM analises WHERE id_analise NOT IN (SELECT id_analise FROM analise_laudos)"
        )
        return cursor.rowcount > 0


def limpar() -> int:
    """Apaga todo o histórico. Devolve quantos laudos foram removidos."""
    with _conectar() as conexao:
        cursor = conexao.execute("DELETE FROM laudos")
        conexao.execute("DELETE FROM analise_laudos")
        conexao.execute("DELETE FROM analises")
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
    "salvar_analise",
    "normalizar_contexto",
    "id_da_imagem",
    "listar",
    "obter",
    "miniatura",
    "remover",
    "limpar",
    "total",
    "como_resultados",
    "Predicao",
]
