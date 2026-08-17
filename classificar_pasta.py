"""Analisa todas as imagens de uma pasta e grava CSV + JSON.

Alternativa em linha de comando ao app, útil para processar o banco de imagens
de uma vez e para medir a acurácia no teste de campo.

Uso:
    py -3.12 classificar_pasta.py dados/exemplos
    py -3.12 classificar_pasta.py dados/campo --motor comparacao
"""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image

from src import diagnostico, exportacao, rotulos, vlm

EXTENSOES = {".png", ".jpg", ".jpeg"}

MOTORES = {
    "auto": diagnostico.MODO_AUTOMATICO,
    "cnn": diagnostico.MODO_CNN,
    "vlm": diagnostico.MODO_VLM,
    "comparacao": diagnostico.MODO_COMPARACAO,
}


def main() -> int:
    analisador = argparse.ArgumentParser(description="Diagnóstico AgroSmart em lote.")
    analisador.add_argument("pasta", type=Path, help="Pasta com as imagens a analisar.")
    analisador.add_argument(
        "--motor",
        choices=sorted(MOTORES),
        default="auto",
        help="auto: CNN e, abaixo do limiar, o generalista. comparacao: os dois em cada imagem.",
    )
    analisador.add_argument(
        "--saida",
        type=Path,
        default=Path("exportacoes"),
        help="Pasta de destino dos arquivos exportados (padrão: exportacoes).",
    )
    analisador.add_argument(
        "--limiar",
        type=float,
        default=rotulos.LIMIAR_CONFIANCA,
        help=f"Confiança mínima do CNN (padrão: {rotulos.LIMIAR_CONFIANCA}).",
    )
    argumentos = analisador.parse_args()

    if not argumentos.pasta.is_dir():
        print(f"ERRO: pasta nao encontrada: {argumentos.pasta}")
        return 1

    caminhos = sorted(p for p in argumentos.pasta.iterdir() if p.suffix.lower() in EXTENSOES)
    if not caminhos:
        print(f"ERRO: nenhuma imagem .png/.jpg/.jpeg em {argumentos.pasta}")
        return 1

    modo = MOTORES[argumentos.motor]
    print(f"Analisando {len(caminhos)} imagem(ns) de {argumentos.pasta} [{modo}]…")

    def progresso(feitos: int, total: int, nome: str) -> None:
        print(f"  ... {feitos}/{total} {nome}")

    try:
        itens = [(caminho.name, Image.open(caminho)) for caminho in caminhos]
        resultados, avisos = diagnostico.analisar_lote(
            itens, modo=modo, limiar=argumentos.limiar, progresso=progresso
        )
    except (vlm.VLMIndisponivelError, Exception) as erro:
        print(f"ERRO: {erro}")
        return 1

    for aviso in avisos:
        print(f"AVISO: {aviso}")

    print()
    for resultado in resultados:
        motor = "CNN" if resultado.do_cnn else "VLM"
        print(
            f"  [{motor}] {resultado.arquivo:<32} {resultado.diagnostico:<28} {resultado.confianca_texto}"
        )

    caminho_csv, caminho_json = exportacao.salvar(
        resultados, argumentos.saida, prefixo=argumentos.pasta.name
    )
    doentes = sum(1 for r in resultados if r.condicao == rotulos.CONDICAO_DOENTE)
    indeterminados = sum(1 for r in resultados if r.indeterminado)

    print(f"\n{len(resultados)} laudo(s) | {doentes} doentes | {indeterminados} indeterminados")
    print(f"CSV : {caminho_csv}")
    print(f"JSON: {caminho_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
