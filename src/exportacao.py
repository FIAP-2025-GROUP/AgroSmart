"""Exportação estruturada dos resultados em CSV e JSON (requisito 1.2)."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Sequence

import pandas as pd

from . import rotulos
from .tipos import MOTOR_CNN, MOTOR_VLM, Resultado

COLUNAS = [
    "arquivo",
    "data_hora",
    "motor",
    "especie",
    "diagnostico",
    "condicao",
    "confianca",
    "confianca_texto",
    "agente_causal",
    "sintomas_observados",
    "manejo_recomendado",
    "alternativa_1",
    "confianca_alternativa_1",
    "alternativa_2",
    "confianca_alternativa_2",
    "observacoes",
    "modelo_versao",
]


def _alternativa(resultado: Resultado, posicao: int) -> tuple[str, float | str]:
    """Segunda e terceira colocadas do ranking do CNN, para auditar a predição.

    Vazio nas linhas do VLM: ele não produz ranking sobre um conjunto fechado
    de classes.
    """
    if len(resultado.ranking) <= posicao:
        return "—", ""
    predicao = resultado.ranking[posicao]
    return predicao.rotulo, round(predicao.confianca, 4)


def para_linhas(resultados: Sequence[Resultado]) -> list[dict]:
    """Achata os resultados no formato tabular usado por ambos os arquivos."""
    linhas = []
    for resultado in resultados:
        alt1_rotulo, alt1_confianca = _alternativa(resultado, 1)
        alt2_rotulo, alt2_confianca = _alternativa(resultado, 2)
        linhas.append(
            {
                "arquivo": resultado.arquivo,
                "data_hora": resultado.data_hora,
                "motor": resultado.motor,
                "especie": resultado.especie,
                "diagnostico": resultado.diagnostico,
                "condicao": resultado.condicao,
                # Só o CNN tem probabilidade real; no VLM a coluna fica vazia
                # em vez de receber um número inventado a partir de "média".
                "confianca": round(resultado.confianca, 4) if resultado.confianca is not None else "",
                "confianca_texto": resultado.confianca_texto,
                "agente_causal": resultado.agente,
                "sintomas_observados": resultado.sintomas,
                "manejo_recomendado": resultado.manejo,
                "alternativa_1": alt1_rotulo,
                "confianca_alternativa_1": alt1_confianca,
                "alternativa_2": alt2_rotulo,
                "confianca_alternativa_2": alt2_confianca,
                "observacoes": resultado.observacoes,
                "modelo_versao": resultado.modelo_versao,
            }
        )
    return linhas


def _metadados(resultados: Sequence[Resultado]) -> dict:
    # As chaves usam os mesmos valores gravados na coluna `condicao`, para que
    # dê para cruzar os dois blocos do JSON sem tradução no meio.
    contagem = {
        condicao: sum(1 for r in resultados if r.condicao == condicao)
        for condicao in (
            rotulos.CONDICAO_SAUDAVEL,
            rotulos.CONDICAO_DOENTE,
            rotulos.CONDICAO_INDETERMINADA,
        )
    }
    por_motor = {
        motor: sum(1 for r in resultados if r.motor == motor)
        for motor in (MOTOR_CNN, MOTOR_VLM)
    }
    return {
        "projeto": "AgroSmart — Fase 1",
        "versao_catalogo": rotulos.VERSAO_CATALOGO,
        "limiar_confianca_cnn": rotulos.LIMIAR_CONFIANCA,
        "data_analise": datetime.now().isoformat(timespec="seconds"),
        "total_laudos": len(resultados),
        "imagens_distintas": len({r.arquivo for r in resultados}),
        "laudos_por_motor": por_motor,
        "contagem_por_condicao": contagem,
    }


def gerar_csv(resultados: Sequence[Resultado]) -> bytes:
    """CSV no dialeto que o Excel em português abre sem ajuste manual.

    Separador ';', decimal ',' e encoding utf-8-sig (o BOM é o que faz o Excel
    reconhecer os acentos). Com vírgula e UTF-8 puro o arquivo abre numa coluna
    só e com os acentos corrompidos.
    """
    tabela = pd.DataFrame(para_linhas(resultados), columns=COLUNAS)
    # to_csv sem `path` devolve str e ignora o parâmetro `encoding`; o BOM vem
    # do encode abaixo.
    texto = tabela.to_csv(index=False, sep=";", decimal=",")
    return texto.encode("utf-8-sig")


def gerar_json(resultados: Sequence[Resultado]) -> bytes:
    """JSON com bloco de metadados + lista de resultados."""
    documento = {
        "metadata": _metadados(resultados),
        "resultados": para_linhas(resultados),
    }
    return json.dumps(documento, ensure_ascii=False, indent=2).encode("utf-8")


def salvar(resultados: Sequence[Resultado], diretorio: Path, prefixo: str = "analise") -> tuple[Path, Path]:
    """Grava as duas versões em disco e devolve os caminhos gerados."""
    diretorio.mkdir(parents=True, exist_ok=True)
    carimbo = datetime.now().strftime("%Y%m%d_%H%M%S")

    caminho_csv = diretorio / f"{prefixo}_{carimbo}.csv"
    caminho_json = diretorio / f"{prefixo}_{carimbo}.json"
    caminho_csv.write_bytes(gerar_csv(resultados))
    caminho_json.write_bytes(gerar_json(resultados))
    return caminho_csv, caminho_json
