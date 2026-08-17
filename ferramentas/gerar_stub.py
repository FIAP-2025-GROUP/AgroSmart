"""Gera um modelo de pesos aleatórios no topo, para testar o app sem o Colab.

O stub tem exatamente a mesma arquitetura, o mesmo formato de entrada e o mesmo
`classes.json` do modelo real — só não sabe diagnosticar nada. Serve para
validar o app, o limiar de rejeição e a exportação antes de o treino terminar.

Uso:  py -3.12 ferramentas/gerar_stub.py
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from src import rotulos  # noqa: E402
from src.arquitetura import TAMANHO_ENTRADA, construir_modelo  # noqa: E402

PASTA_MODELO = RAIZ / "modelo"


def main() -> None:
    PASTA_MODELO.mkdir(parents=True, exist_ok=True)

    print(f"Construindo stub com {len(rotulos.IDS_ORDENADOS)} classes…")
    modelo = construir_modelo(len(rotulos.IDS_ORDENADOS))

    caminho_modelo = PASTA_MODELO / "agrosmart_mobilenetv2.keras"
    modelo.save(caminho_modelo)

    metadados = {
        "versao_catalogo": rotulos.VERSAO_CATALOGO,
        "arquitetura": "MobileNetV2 (STUB — pesos do topo aleatórios)",
        "tamanho_entrada": list(TAMANHO_ENTRADA),
        "treinado_em": datetime.now().isoformat(timespec="seconds"),
        "acuracia_teste": None,
        "ids": rotulos.IDS_ORDENADOS,
    }
    caminho_classes = PASTA_MODELO / "classes.json"
    caminho_classes.write_text(
        json.dumps(metadados, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    tamanho_mb = caminho_modelo.stat().st_size / 1_000_000
    print(f"OK  {caminho_modelo}  ({tamanho_mb:.1f} MB)")
    print(f"OK  {caminho_classes}")
    print("\nATENCAO: stub sem treino. Os diagnosticos serao aleatorios ate voce")
    print("substituir estes arquivos pelos gerados no notebook do Colab.")


if __name__ == "__main__":
    main()
