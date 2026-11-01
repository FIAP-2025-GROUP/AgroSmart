"""Armazenamento da Fase 3: um SQLite com estado, dados estruturados e ações.

Por que SQLite: é um arquivo só, transacional, indexado e já usado pelo app
desde a Fase 1. O que precisa ser atômico fica numa transação:

* os registros novos de um arquivo **e** o avanço do checkpoint daquele arquivo
  (se o processo cair no meio, nem os registros nem o checkpoint avançam);
* um alerta **e** as ações que ele dispara (padrão *outbox*: a ação nasce
  `pendente` junto com o alerta e só depois é executada e marcada).

Tabelas:

    arquivos      checkpoint por arquivo: bytes e linhas já consumidos
    laudos        laudos de diagnóstico normalizados (1 por id de laudo)
    leituras      leituras de sensor (1 por id de leitura)
    rejeitados    linhas recusadas, com motivo
    lotes         um registro por micro-batch que encontrou dados novos
    alertas       decisões das regras (chave de deduplicação única)
    acoes         o que cada alerta disparou e se já foi executado
    irrigacao     estado do simulador de irrigação por talhão
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

ESQUEMA = """
CREATE TABLE IF NOT EXISTS arquivos (
    nome            TEXT PRIMARY KEY,
    tipo            TEXT NOT NULL,
    bytes_lidos     INTEGER NOT NULL DEFAULT 0,
    linhas_lidas    INTEGER NOT NULL DEFAULT 0,
    assinatura      TEXT,              -- SHA-256 dos primeiros bytes já lidos
    cabecalho       TEXT,              -- cabeçalho do CSV, para retomar no meio
    primeira_leitura TEXT NOT NULL,
    ultima_leitura  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS laudos (
    chave           TEXT PRIMARY KEY,  -- origem_dado|origem_instancia|id_laudo
    id_laudo        TEXT NOT NULL,
    id_analise      TEXT,
    instante        TEXT NOT NULL,
    dia             TEXT NOT NULL,
    uf TEXT, municipio TEXT, propriedade TEXT, talhao TEXT,
    cultura         TEXT,
    condicao        TEXT NOT NULL,
    diagnostico     TEXT,
    categoria       TEXT,
    motor           TEXT NOT NULL,
    confianca       REAL,
    manejo          TEXT,
    origem_dado     TEXT NOT NULL,
    arquivo         TEXT NOT NULL,
    id_lote         INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_laudos_talhao_dia ON laudos (propriedade, talhao, dia);

CREATE TABLE IF NOT EXISTS leituras (
    id_leitura      TEXT PRIMARY KEY,
    instante        TEXT NOT NULL,
    id_sensor       TEXT NOT NULL,
    uf TEXT, municipio TEXT,
    propriedade     TEXT NOT NULL,
    talhao          TEXT NOT NULL,
    cultura         TEXT NOT NULL,
    umidade_solo_pct REAL NOT NULL,
    temperatura_c   REAL NOT NULL,
    umidade_ar_pct  REAL NOT NULL,
    chuva_mm        REAL NOT NULL,
    pragas_armadilha INTEGER NOT NULL,
    arquivo         TEXT NOT NULL,
    id_lote         INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_leituras_talhao_instante ON leituras (propriedade, talhao, instante);

CREATE TABLE IF NOT EXISTS rejeitados (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    arquivo         TEXT NOT NULL,
    linha           INTEGER NOT NULL,
    motivos         TEXT NOT NULL,
    conteudo        TEXT,
    id_lote         INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS lotes (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    iniciado_em     TEXT NOT NULL,
    concluido_em    TEXT,
    arquivos        TEXT NOT NULL DEFAULT '[]',
    novos           INTEGER NOT NULL DEFAULT 0,
    duplicados      INTEGER NOT NULL DEFAULT 0,
    rejeitados      INTEGER NOT NULL DEFAULT 0,
    alertas         INTEGER NOT NULL DEFAULT 0,
    acoes           INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS alertas (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    chave_dedup     TEXT NOT NULL UNIQUE,
    regra           TEXT NOT NULL,
    severidade      TEXT NOT NULL,
    propriedade     TEXT, talhao TEXT, cultura TEXT, uf TEXT, municipio TEXT,
    titulo          TEXT NOT NULL,
    mensagem        TEXT NOT NULL,
    recomendacao    TEXT NOT NULL,
    evidencias      TEXT NOT NULL,     -- JSON
    instante_evento TEXT NOT NULL,     -- tempo do dado que disparou
    criado_em       TEXT NOT NULL,     -- tempo do processamento
    id_lote         INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_alertas_regra_talhao ON alertas (regra, propriedade, talhao, instante_evento);

CREATE TABLE IF NOT EXISTS acoes (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    id_alerta       INTEGER NOT NULL REFERENCES alertas(id),
    tipo            TEXT NOT NULL,     -- arquivo_alerta, email, relatorio, irrigacao, painel
    status          TEXT NOT NULL,     -- pendente, executando, executada, falhou
    destino         TEXT,              -- arquivo gerado ou destinatário
    detalhe         TEXT,
    criada_em       TEXT NOT NULL,
    executada_em    TEXT,
    tentativas      INTEGER NOT NULL DEFAULT 0,
    UNIQUE (id_alerta, tipo)
);

CREATE TABLE IF NOT EXISTS irrigacao (
    propriedade     TEXT NOT NULL,
    talhao          TEXT NOT NULL,
    ligada          INTEGER NOT NULL,
    desde           TEXT NOT NULL,
    motivo          TEXT NOT NULL,
    PRIMARY KEY (propriedade, talhao)
);
"""


def conectar(caminho: Path) -> sqlite3.Connection:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    conexao = sqlite3.connect(caminho, timeout=10, isolation_level=None)  # transações explícitas
    conexao.row_factory = sqlite3.Row
    conexao.execute("PRAGMA journal_mode=WAL")  # o painel lê enquanto o monitor grava
    conexao.execute("PRAGMA foreign_keys=ON")
    conexao.executescript(ESQUEMA)
    colunas = {r[1] for r in conexao.execute("PRAGMA table_info(acoes)")}
    if "tentativas" not in colunas:  # banco criado por uma versão anterior
        conexao.execute("ALTER TABLE acoes ADD COLUMN tentativas INTEGER NOT NULL DEFAULT 0")
    return conexao


def conectar_leitura(caminho: Path) -> sqlite3.Connection | None:
    """Conexão somente leitura para o painel. Banco ausente → None."""
    if not caminho.is_file():
        return None
    # as_uri() codifica "#", "%" e espaços do caminho (sem isso o SQLite corta a URI no "#").
    conexao = sqlite3.connect(f"{caminho.resolve().as_uri()}?mode=ro", uri=True, timeout=5)
    conexao.row_factory = sqlite3.Row
    return conexao


@contextmanager
def transacao(conexao: sqlite3.Connection):
    conexao.execute("BEGIN IMMEDIATE")
    try:
        yield conexao
    except BaseException:
        conexao.execute("ROLLBACK")
        raise
    conexao.execute("COMMIT")


def linhas(cursor) -> list[dict]:
    return [dict(r) for r in cursor.fetchall()]


def json_texto(valor) -> str:
    return json.dumps(valor, ensure_ascii=False, sort_keys=True, default=str)
