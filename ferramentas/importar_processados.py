"""Importa um pacote processado (por exemplo, exportado do Databricks).

Execução:
    .venv\\Scripts\\python.exe ferramentas\\importar_processados.py exec_20261001T120000Z_ab12cd.zip
    .venv\\Scripts\\python.exe ferramentas\\importar_processados.py C:\\caminho\\exec_20261001T120000Z_ab12cd
    .venv\\Scripts\\python.exe ferramentas\\importar_processados.py pacote.zip --nao-marcar-atual

Aceita o `.zip` gerado pelo notebook `notebooks/pipeline_fase2_databricks.ipynb`
ou a pasta já descompactada. O pacote é validado (manifesto `completo`, hashes,
schema) ANTES de ser copiado e de novo depois; só então vira o pacote atual
servido pelo dashboard. Um pacote inválido é recusado sem tocar no atual.

Não precisa de Java nem de PySpark.
"""

from __future__ import annotations

import argparse
import secrets
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from src.pipeline import contratos as c  # noqa: E402
from src.pipeline import pacote  # noqa: E402


def _achar_pacote(base: Path) -> Path:
    """A pasta que contém `manifesto.json` (na raiz ou um nível abaixo)."""
    if (base / c.MANIFESTO).is_file():
        return base
    candidatos = [p for p in base.iterdir() if p.is_dir() and (p / c.MANIFESTO).is_file()]
    if len(candidatos) != 1:
        raise pacote.PacoteInvalidoError(
            f"Esperava exatamente um pacote em {base}, encontrei {len(candidatos)}.")
    return candidatos[0]


def importar(origem: Path, destino: Path | None = None, marcar_atual: bool = True) -> Path:
    """Valida, copia e (opcionalmente) marca o pacote como atual.

    Se já existir uma execução com o mesmo `id_execucao`:
    * conteúdo idêntico (manifesto byte a byte igual, logo os mesmos hashes)
      → importação idempotente, nada é copiado;
    * conteúdo diferente, ou pacote existente inválido → `ColisaoPacoteError`,
      sem tocar no que existe e sem trocar o pacote atual.
    """
    origem = Path(origem)
    destino = Path(destino or c.PASTA_PROCESSADOS)
    destino.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as temporaria:
        if origem.suffix.lower() == ".zip":
            with zipfile.ZipFile(origem) as arquivo_zip:
                for nome in arquivo_zip.namelist():  # nada de caminhos fora da pasta
                    partes = Path(nome).parts
                    if nome.startswith(("/", "\\")) or ".." in partes or ":" in nome:
                        raise pacote.PacoteInvalidoError(f"Caminho suspeito no zip: {nome}")
                arquivo_zip.extractall(temporaria)
            pasta = _achar_pacote(Path(temporaria))
        else:
            pasta = _achar_pacote(origem)

        manifesto = pacote.ler(pasta).manifesto
        id_execucao = manifesto["id_execucao"]
        final = pacote.pasta_execucao(destino, id_execucao)
        if final.exists():
            try:
                pacote.validar(final)
            except pacote.PacoteInvalidoError as erro:
                raise pacote.ColisaoPacoteError(
                    f"Já existe {id_execucao} em {destino}, mas ele é inválido ({erro}). "
                    "Remova-o manualmente antes de importar.") from None
            if not pacote.mesmo_conteudo(final, pasta):
                raise pacote.ColisaoPacoteError(
                    f"Já existe uma execução {id_execucao} com conteúdo diferente do pacote recebido. "
                    "Nada foi alterado.")
            print(f"A execução {id_execucao} já estava importada com o mesmo conteúdo.")
        else:
            parcial = destino / f".tmp_import_{id_execucao}_{secrets.token_hex(4)}"
            try:
                shutil.copytree(pasta, parcial)
                pacote.ler(parcial, nome_esperado=id_execucao)
                parcial.rename(final)
            finally:
                shutil.rmtree(parcial, ignore_errors=True)
            pacote.validar(final)

    if marcar_atual:
        pacote.marcar_atual(id_execucao, destino)
    return final


def main() -> None:
    analisador = argparse.ArgumentParser(description="Importa um pacote processado da Fase 2.")
    analisador.add_argument("origem", type=Path, help="Arquivo .zip ou pasta do pacote.")
    analisador.add_argument("--destino", type=Path, default=None)
    analisador.add_argument("--nao-marcar-atual", action="store_true",
                            help="Importa sem trocar o pacote servido pelo dashboard.")
    argumentos = analisador.parse_args()

    try:
        final = importar(argumentos.origem, argumentos.destino, not argumentos.nao_marcar_atual)
    except pacote.PacoteInvalidoError as erro:
        sys.exit(f"Pacote recusado: {erro}")
    manifesto = pacote.validar(final)
    print(f"Pacote {manifesto['id_execucao']} importado em {final}")
    print(f"  processado em {manifesto.get('processado_em')} ({manifesto.get('ambiente')})")
    print(f"  por origem: {manifesto.get('por_origem')}")
    if not argumentos.nao_marcar_atual:
        print("  marcado como atual — recarregue o dashboard.")


if __name__ == "__main__":
    main()
