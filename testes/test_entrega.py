"""Empacotador da entrega: o que entra, o que nunca entra e o pacote embarcado."""

from __future__ import annotations

import json

import pytest

import empacotar_entrega as e
from src.pipeline import contratos as c
from test_api_analitica import analise, laudos_de, publicar


@pytest.mark.parametrize(("relativo", "entra"), [
    ("servidor.py", True),
    ("web/dashboard.js", True),
    ("notebooks/pipeline_fase2_databricks.ipynb", True),
    ("dados/simulados/laudos_simulados.jsonl", True),
    ("testes/fixtures/raw_basico/laudos.jsonl", True),
    (".env.example", True),
    (".env", False),
    (".env.producao", False),
    ("dados/historico.db", False),
    ("dados/auxiliar.sqlite", False),
    ("dados/raw/snap_x/laudos.jsonl", False),
    ("dados/bronze/exec_x/bronze/parte-00000.jsonl", False),
    ("dados/silver/exec_x/silver/parte-00000.jsonl", False),
    ("dados/gold/exec_x/gold_analises/parte-00000.jsonl", False),
    ("dados/processados/exec_x/laudos.jsonl", False),   # entra só pelo caminho validado
    ("dados/campo/foto.jpg", False),
    ("src/__pycache__/x.cpython-312.pyc", False),
    (".pytest_cache/v/cache/nodeids", False),
    ("entrega/antiga.zip", False),
    ("relatorio/~$rascunho.docx", False),
])
def test_filtro_de_arquivos(relativo, entra):
    assert e.deve_entrar(e.RAIZ / relativo) is entra


def test_recusa_pacote_com_dados_reais():
    a = analise("r1", origem="real")
    publicar([a, analise("s1")], laudos_de(a) + laudos_de(analise("s1")), id_execucao="exec_misto")
    with pytest.raises(SystemExit, match="simulado"):
        e.pacote_simulado()


def test_aceita_pacote_exclusivamente_simulado():
    a = analise("s1")
    publicar([a], laudos_de(a), id_execucao="exec_sim")
    assert e.pacote_simulado().manifesto["id_execucao"] == "exec_sim"


def test_recusa_sem_ponteiro_ou_com_ponteiro_malicioso():
    with pytest.raises(SystemExit):
        e.pacote_simulado()
    c.PASTA_PROCESSADOS.mkdir(parents=True, exist_ok=True)
    (c.PASTA_PROCESSADOS / "ATUAL.json").write_text(json.dumps({"id_execucao": "../../x"}), encoding="utf-8")
    with pytest.raises(SystemExit, match="inválido"):
        e.pacote_simulado()
