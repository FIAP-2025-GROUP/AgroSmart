"""Monta o .zip da entrega acadêmica (Fase 2).

    .venv\\Scripts\\python.exe ferramentas\\empacotar_entrega.py
    .venv\\Scripts\\python.exe ferramentas\\empacotar_entrega.py --saida C:\\temp\\teste.zip

Estrutura do pacote:

    LEIA-ME.txt          mapa do pacote e como executar
    LINK_DO_VIDEO.txt    endereço do vídeo (preencher)
    projeto/             código, painel, notebook, documentação, testes, gerador,
                         dados simulados e UM pacote processado só com dados simulados

O que NUNCA entra, por motivo:

* segredos — `.env`, `.env.*` (exceto `.env.example`);
* dados operacionais ou privados — `historico.db` e qualquer SQLite/banco
  auxiliar, `dados/campo/` (fotos reais), snapshots RAW e camadas
  Bronze/Silver/Gold intermediárias, exportações do pipeline;
* pacotes processados que não sejam exclusivamente simulados;
* caches e temporários — `.venv`, `__pycache__`, `.pytest_cache`, `.tmp_*`,
  arquivos de trava do Word, `.zip` antigos.

O pacote processado embarcado é o apontado por `dados/processados/ATUAL.json`.
Ele precisa passar na validação completa e conter apenas `origem_dado =
"simulado"`; se não, o empacotamento é recusado. Gere-o antes com:

    .venv\\Scripts\\python.exe ferramentas\\executar_pipeline.py --origem simulado
"""

from __future__ import annotations

import argparse
import json
import sys
import zipfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from src.pipeline import contratos as c  # noqa: E402
from src.pipeline import pacote  # noqa: E402

DESTINO_PADRAO = RAIZ / "entrega" / "paulo_sergio_morais_RM553012_3ESOR_fase2_atividade.zip"

# Pastas (em qualquer nível) que não vão no pacote.
PASTAS_FORA = {
    ".venv", "__pycache__", ".pytest_cache", ".git", ".claude", ".idea", ".vscode",
    "entrega",          # o próprio destino e as entregas anteriores
    "exportacoes",      # saídas geradas pelo app
    "node_modules",
}
# Caminhos relativos (a partir da raiz) que não vão, nem o que houver dentro.
PREFIXOS_FORA = (
    "dados/raw/", "dados/bronze/", "dados/silver/", "dados/gold/", "dados/exportacoes/",
    "dados/campo/",       # fotos reais de celular
    "dados/processados/",  # entra só o pacote simulado validado, tratado à parte
)
ARQUIVOS_FORA = {".env", ".env.local", "historico.db", "Thumbs.db", ".DS_Store"}
SUFIXOS_FORA = {".pyc", ".zip", ".pdf", ".db", ".sqlite", ".sqlite3", ".db-journal",
                ".db-wal", ".db-shm", ".tmp", ".log"}

AVISO_VIDEO = """\
VÍDEO DA APLICAÇÃO — AgroSmart · Fase 2
Paulo Sergio Morais · RM 553012 · 3ESOR

Link: «COLE AQUI O LINK DO VÍDEO»

Roteiro e números usados: projeto/docs/fase2_roteiro_video.md
"""


def _mapa_do_pacote(id_execucao: str) -> str:
    return f"""\
ENTREGA — AgroSmart
Paulo Sergio Morais · RM 553012 · 3ESOR
Fase 2 — Painel analítico, pipeline Spark e Databricks

CONTEÚDO
--------
LINK_DO_VIDEO.txt    endereço do vídeo de apresentação
projeto/             código-fonte completo

  projeto/web/dashboard.*                     painel analítico (/dashboard)
  projeto/src/pipeline/, projeto/src/analitica.py   pipeline e API analítica
  projeto/notebooks/pipeline_fase2_databricks.ipynb notebook do Databricks
  projeto/docs/fase2_*.md                     arquitetura, dados/execução, roteiro do vídeo
  projeto/testes/                             testes automatizados (pytest)
  projeto/dados/simulados/                    dados de demonstração (semente fixa)
  projeto/dados/processados/{id_execucao}/
                                              pacote processado pronto para o painel

DADOS
-----
O painel abre com dados SIMULADOS, identificados como tal na tela. Eles não
representam a situação de nenhuma lavoura. Nenhum dado operacional real
(histórico SQLite, fotos, snapshots) acompanha este pacote.

COMO EXECUTAR (Windows)
-----------------------
  cd projeto
  py -3.12 -m venv .venv
  .venv\\Scripts\\python.exe -m pip install -r requirements.txt
  .venv\\Scripts\\python.exe servidor.py
  -> http://localhost:8000            diagnóstico (Fase 1)
  -> http://localhost:8000/dashboard  painel analítico (Fase 2)

O painel não precisa de Java nem de Spark. Para reprocessar os dados:
  .venv\\Scripts\\python.exe -m pip install -r requirements-spark.txt   (Java 17 em JAVA_HOME)
  .venv\\Scripts\\python.exe ferramentas\\executar_pipeline.py --origem simulado
Testes: .venv\\Scripts\\python.exe -m pip install -r requirements-dev.txt
        .venv\\Scripts\\python.exe -m pytest

A chave do Gemini (.env) NÃO acompanha o pacote; veja projeto/.env.example.
"""


def _relativo(caminho: Path) -> str:
    return caminho.relative_to(RAIZ).as_posix()


def deve_entrar(caminho: Path) -> bool:
    relativo = _relativo(caminho)
    if set(Path(relativo).parts) & PASTAS_FORA:
        return False
    if relativo.startswith(PREFIXOS_FORA):
        return False
    nome = caminho.name
    if nome in ARQUIVOS_FORA or nome.startswith(("~$", ".tmp_")):
        return False
    if nome.startswith(".env") and nome != ".env.example":
        return False
    return caminho.suffix.lower() not in SUFIXOS_FORA


def pacote_simulado(destino: Path = None) -> pacote.PacoteLido:
    """O pacote atual, validado e exclusivamente simulado — ou erro explícito."""
    destino = Path(destino or c.PASTA_PROCESSADOS)
    ponteiro = destino / c.PONTEIRO_ATUAL
    if not ponteiro.is_file():
        raise SystemExit("Sem dados/processados/ATUAL.json. Rode executar_pipeline.py --origem simulado.")
    try:
        id_execucao = json.loads(ponteiro.read_text(encoding="utf-8"))["id_execucao"]
        lido = pacote.ler(pacote.pasta_execucao(destino, id_execucao))
    except (pacote.PacoteInvalidoError, KeyError, TypeError, json.JSONDecodeError) as erro:
        raise SystemExit(f"Pacote atual inválido: {erro}") from None
    origens = {r["origem_dado"] for r in lido.analises + lido.laudos}
    origens |= {m["origem_dado"] for m in lido.qualidade.get("metricas", [])} - {"desconhecida"}
    if origens - {c.ORIGEM_SIMULADO}:
        raise SystemExit(
            f"O pacote atual ({id_execucao}) contém origens {sorted(origens)}. A entrega só leva "
            "dados simulados: rode executar_pipeline.py --origem simulado e tente de novo.")
    return lido


def empacotar(saida: Path = DESTINO_PADRAO) -> Path:
    lido = pacote_simulado()
    id_execucao = lido.manifesto["id_execucao"]
    saida = Path(saida)
    saida.parent.mkdir(parents=True, exist_ok=True)
    if saida.exists():
        saida.unlink()

    with zipfile.ZipFile(saida, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        z.writestr("LEIA-ME.txt", _mapa_do_pacote(id_execucao))
        z.writestr("LINK_DO_VIDEO.txt", AVISO_VIDEO)

        for arquivo in sorted(RAIZ.rglob("*")):
            if arquivo.is_file() and arquivo.resolve() != saida.resolve() and deve_entrar(arquivo):
                z.write(arquivo, f"projeto/{_relativo(arquivo)}")

        # Só o pacote simulado validado, e um ponteiro que aponta para ele.
        for nome in (c.MANIFESTO, *c.ARQUIVOS_PACOTE):
            z.write(lido.pasta / nome, f"projeto/dados/processados/{id_execucao}/{nome}")
        z.writestr(f"projeto/dados/processados/{c.PONTEIRO_ATUAL}",
                   json.dumps({"id_execucao": id_execucao}))
    return saida


def verificar(caminho: Path) -> list[str]:
    """Problemas encontrados no zip (lista vazia = pacote limpo)."""
    problemas = []
    with zipfile.ZipFile(caminho) as z:
        nomes = z.namelist()
    for nome in nomes:
        base = nome.rsplit("/", 1)[-1]
        if (base.startswith(".env") and base != ".env.example") or base == "historico.db" \
                or base.lower().endswith((".db", ".sqlite", ".sqlite3")):
            problemas.append(f"segredo/banco: {nome}")
        if any(f"/{p}" in f"/{nome}" for p in ("dados/raw/", "dados/bronze/", "dados/silver/",
                                                "dados/gold/", "dados/campo/", "__pycache__/", ".tmp_")):
            problemas.append(f"intermediário/temporário: {nome}")
    pacotes = {n.split("/")[3] for n in nomes if n.startswith("projeto/dados/processados/exec_")}
    if len(pacotes) != 1:
        problemas.append(f"esperado 1 pacote processado, encontrados {sorted(pacotes)}")
    obrigatorios = ["projeto/servidor.py", "projeto/web/dashboard.html", "projeto/src/analitica.py",
                    "projeto/notebooks/pipeline_fase2_databricks.ipynb", "projeto/requirements.txt",
                    "projeto/requirements-spark.txt", "projeto/requirements-dev.txt",
                    "projeto/dados/simulados/laudos_simulados.jsonl",
                    "projeto/dados/processados/ATUAL.json", "projeto/ferramentas/gerar_dados_simulados.py",
                    "projeto/docs/fase2_roteiro_video.md", "projeto/testes/test_api_analitica.py",
                    "projeto/modelo/agrosmart_mobilenetv2.keras"]
    problemas += [f"faltando: {n}" for n in obrigatorios if n not in nomes]
    return problemas


def main() -> None:
    analisador = argparse.ArgumentParser(description="Monta o .zip da entrega da Fase 2.")
    analisador.add_argument("--saida", type=Path, default=DESTINO_PADRAO)
    argumentos = analisador.parse_args()

    caminho = empacotar(argumentos.saida)
    with zipfile.ZipFile(caminho) as z:
        nomes = z.namelist()
        tamanho = sum(i.file_size for i in z.infolist())
    print(f"\n  {caminho}")
    print(f"  {len(nomes)} arquivos · {caminho.stat().st_size / 1024:.0f} KB comprimidos "
          f"(de {tamanho / 1024:.0f} KB)\n")
    problemas = verificar(caminho)
    if problemas:
        print("  PROBLEMAS:\n    " + "\n    ".join(problemas))
        sys.exit(1)
    print("  Verificado: sem segredos, sem banco operacional, sem camadas intermediárias,")
    print("  um único pacote processado (exclusivamente simulado) e ATUAL.json apontando para ele.")


if __name__ == "__main__":
    main()
