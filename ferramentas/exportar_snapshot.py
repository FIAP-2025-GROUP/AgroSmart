"""Cria um snapshot RAW a partir do histórico real e/ou dos dados simulados.

Execução:
    .venv\\Scripts\\python.exe ferramentas\\exportar_snapshot.py --origem ambos
    .venv\\Scripts\\python.exe ferramentas\\exportar_snapshot.py --origem real
    .venv\\Scripts\\python.exe ferramentas\\exportar_snapshot.py --origem simulado

Saída: `dados/raw/<id_snapshot>/{manifesto.json, laudos.jsonl}`. Esta é a pasta
que se envia ao Databricks (upload para um Volume) quando o pipeline não roda
localmente.

O histórico SQLite é aberto em modo somente leitura e nada nele é alterado.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from src.pipeline import contratos as c  # noqa: E402
from src.pipeline import ingestao  # noqa: E402

BANCO_PADRAO = c.PASTA_DADOS / "historico.db"


def exportar(origem: str, banco: Path = BANCO_PADRAO, simulados: Path = c.ARQUIVO_SIMULADOS,
             destino: Path = c.PASTA_RAW) -> Path:
    registros: list[dict] = []
    fontes: list[dict] = []

    if origem in ("real", "ambos"):
        reais = ingestao.ler_historico_sqlite(banco)
        registros += reais
        fontes.append({"origem_dado": c.ORIGEM_REAL, "tipo": "sqlite_fase1",
                       "caminho": str(Path(banco).name), "registros": len(reais),
                       "observacao": "histórico legado: sem id_analise, id_imagem nem localidade"})

    if origem in ("simulado", "ambos"):
        simulados_lidos = ingestao.ler_simulados(simulados)
        registros += simulados_lidos
        fontes.append({"origem_dado": c.ORIGEM_SIMULADO, "tipo": "jsonl_gerador",
                       "caminho": str(Path(simulados).name), "registros": len(simulados_lidos),
                       "observacao": "dados sintéticos para demonstração — não são incidência real"})

    return ingestao.criar_snapshot(registros, fontes, destino=destino)


def main() -> None:
    analisador = argparse.ArgumentParser(description="Cria um snapshot RAW do AgroSmart.")
    analisador.add_argument("--origem", choices=["real", "simulado", "ambos"], default="ambos")
    analisador.add_argument("--banco", type=Path, default=BANCO_PADRAO)
    analisador.add_argument("--simulados", type=Path, default=c.ARQUIVO_SIMULADOS)
    analisador.add_argument("--destino", type=Path, default=c.PASTA_RAW)
    argumentos = analisador.parse_args()

    pasta = exportar(argumentos.origem, argumentos.banco, argumentos.simulados, argumentos.destino)
    manifesto = ingestao.validar_snapshot(pasta)
    print(f"Snapshot {manifesto['id_snapshot']}: {manifesto['quantidade_registros']} laudos "
          f"{manifesto['registros_por_origem']}")
    print(f"  {pasta}")


if __name__ == "__main__":
    main()
