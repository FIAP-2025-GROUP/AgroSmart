"""Orquestração dos dois motores de diagnóstico.

Decide qual motor responde por cada imagem e devolve tudo numa lista única de
`Resultado`. No modo de comparação, a mesma imagem aparece duas vezes — uma por
motor — o que é exatamente o formato desejado para a tabela do relatório.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Sequence

from PIL import Image

from . import inferencia, rotulos, vlm
from .tipos import Resultado

MODO_AUTOMATICO = "Automático (CNN → VLM)"
MODO_CNN = "Somente CNN especializado"
MODO_VLM = "Somente visão generalista"
MODO_COMPARACAO = "Comparar os dois motores"

MODOS = [MODO_AUTOMATICO, MODO_CNN, MODO_VLM, MODO_COMPARACAO]

# A camada gratuita do Gemini limita requisições por minuto; três chamadas
# simultâneas cortam o tempo de espera sem chegar perto do teto.
CONCORRENCIA_VLM = 3

Item = tuple[str, Image.Image]
Progresso = Callable[[int, int, str], None]


def _rodar_vlm(itens: Sequence[Item], chave: str | None, progresso: Progresso | None) -> list[Resultado]:
    """Consulta o VLM para cada imagem, preservando a ordem de entrada.

    O callback `progresso` é chamado apenas na thread principal, ao colher cada
    future. Chamá-lo de dentro das workers quebraria a interface: widget do
    Streamlit tocado fora da thread principal lança exceção sem mensagem.
    """
    if not itens:
        return []

    total = len(itens)
    resultados: list[Resultado] = []

    with ThreadPoolExecutor(max_workers=CONCORRENCIA_VLM) as executor:
        futuros = [executor.submit(vlm.analisar, nome, imagem, chave) for nome, imagem in itens]
        for posicao, futuro in enumerate(futuros):
            resultados.append(futuro.result())
            if progresso:
                progresso(posicao + 1, total, itens[posicao][0])

    return resultados


def analisar_lote(
    itens: Sequence[Item],
    modo: str = MODO_AUTOMATICO,
    limiar: float = rotulos.LIMIAR_CONFIANCA,
    chave_vlm: str | None = None,
    progresso: Progresso | None = None,
) -> tuple[list[Resultado], list[str]]:
    """Analisa um lote e devolve (resultados, avisos).

    Os avisos carregam falhas não fatais — motor indisponível, cota esgotada —
    para que a interface possa mostrá-las sem perder os resultados do outro
    motor.
    """
    if not itens:
        return [], []

    avisos: list[str] = []
    resultados_cnn: dict[str, Resultado] = {}

    # ------------------------------------------------------------------ CNN
    if modo in (MODO_AUTOMATICO, MODO_CNN, MODO_COMPARACAO):
        try:
            for resultado in inferencia.predizer_lote(itens, limiar=limiar):
                resultados_cnn[resultado.arquivo] = resultado
        except inferencia.ModeloIndisponivelError as erro:
            if modo == MODO_CNN:
                raise
            avisos.append(f"Motor CNN indisponível, seguindo só com o generalista. {erro}")

    if modo == MODO_CNN:
        return [resultados_cnn[nome] for nome, _ in itens], avisos

    # ------------------------------------------------------------------ VLM
    if modo == MODO_VLM:
        alvos = list(itens)
    elif modo == MODO_COMPARACAO:
        alvos = list(itens)
    else:  # automático: só o que o CNN não soube responder
        alvos = [
            (nome, imagem)
            for nome, imagem in itens
            if nome not in resultados_cnn or resultados_cnn[nome].indeterminado
        ]

    resultados_vlm: dict[str, Resultado] = {}
    if alvos:
        try:
            for resultado in _rodar_vlm(alvos, chave_vlm, progresso):
                resultados_vlm[resultado.arquivo] = resultado
        except vlm.VLMIndisponivelError as erro:
            if not resultados_cnn:
                raise
            avisos.append(f"Motor de visão generalista indisponível. {erro}")

    # -------------------------------------------------------------- montagem
    resultados: list[Resultado] = []
    for nome, _ in itens:
        if modo == MODO_COMPARACAO:
            resultados.extend(r for r in (resultados_cnn.get(nome), resultados_vlm.get(nome)) if r)
        elif modo == MODO_VLM:
            if nome in resultados_vlm:
                resultados.append(resultados_vlm[nome])
        else:  # automático — o laudo do VLM substitui o indeterminado do CNN
            escolhido = resultados_vlm.get(nome) or resultados_cnn.get(nome)
            if escolhido:
                resultados.append(escolhido)

    return resultados, avisos
