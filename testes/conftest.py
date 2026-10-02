"""Configuração comum dos testes da Fase 2.

Nenhum teste toca o banco operacional (`dados/historico.db`) nem o pacote
processado real (`dados/processados/`): o fixture automático redireciona os
dois para uma pasta temporária. Nada aqui carrega o TensorFlow, o modelo Keras
ou o Gemini.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "ferramentas"))

FIXTURES = RAIZ / "testes" / "fixtures"
RAW_BASICO = FIXTURES / "raw_basico" / "laudos.jsonl"


@pytest.fixture(autouse=True)
def isolar_dados(tmp_path, monkeypatch):
    from src import analitica, analitica_operacional, historico
    from src.pipeline import contratos

    monkeypatch.setattr(historico, "CAMINHO_BANCO", tmp_path / "historico.db")
    monkeypatch.setattr(contratos, "PASTA_PROCESSADOS", tmp_path / "processados")
    analitica._cache.clear()
    analitica_operacional._cache.clear()
    yield
    analitica._cache.clear()
    analitica_operacional._cache.clear()


def _java_disponivel() -> bool:
    # Configuração suportada: Java 17 via JAVA_HOME (ou java no PATH).
    return bool(os.environ.get("JAVA_HOME") or shutil.which("java"))


@pytest.fixture(scope="session")
def spark():
    """SparkSession local. Pula os testes de Spark se PySpark ou Java faltarem."""
    pytest.importorskip("pyspark")
    if not _java_disponivel():
        pytest.skip("Java não encontrado (JAVA_HOME); testes de Spark pulados.")
    os.environ.setdefault("PYSPARK_PYTHON", sys.executable)
    os.environ.setdefault("PYSPARK_DRIVER_PYTHON", sys.executable)
    from src.pipeline import transformacoes_spark as ts

    sessao = ts.criar_sessao_local("agrosmart-testes")
    sessao.sparkContext.setLogLevel("ERROR")
    yield sessao
    sessao.stop()
