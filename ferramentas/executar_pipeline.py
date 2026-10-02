"""Executa o pipeline da Fase 2 localmente: RAW → Bronze → Silver → Gold → pacote.

Execução (precisa de Java 17 com JAVA_HOME e de `requirements-spark.txt`):
    .venv\\Scripts\\python.exe ferramentas\\executar_pipeline.py
    .venv\\Scripts\\python.exe ferramentas\\executar_pipeline.py --origem simulado
    .venv\\Scripts\\python.exe ferramentas\\executar_pipeline.py --snapshot dados\\raw\\snap_...

Sem `--snapshot`, cria antes um snapshot novo da origem pedida (gerando os
dados simulados se ainda não existirem). No fim publica
`dados/processados/<id_execucao>/` e o marca como atual — basta recarregar o
dashboard.

Camadas intermediárias em `dados/{bronze,silver,gold}/<id_execucao>/`:
Parquet quando o Hadoop nativo está disponível; JSON Lines no Windows sem
`winutils.exe` (o Spark processa tudo do mesmo jeito; só a gravação final
dessas cópias de inspeção passa pelo driver). No Databricks as camadas são
tabelas Delta — ver o notebook.

O servidor web não importa este script nem o Spark.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "ferramentas"))

from src.pipeline import contratos as c  # noqa: E402
from src.pipeline import ingestao  # noqa: E402

JAVA_SUPORTADO = 17  # PySpark 3.5 é suportado oficialmente com Java 8, 11 e 17


def _versao_java(executavel: str) -> int | None:
    import re
    import subprocess

    try:
        saida = subprocess.run([executavel, "-version"], capture_output=True, text=True, timeout=30).stderr
    except (OSError, subprocess.SubprocessError):
        return None
    achado = re.search(r'version "(\d+)(?:\.(\d+))?', saida)
    if not achado:
        return None
    principal = int(achado.group(1))
    return int(achado.group(2)) if principal == 1 and achado.group(2) else principal


def preparar_java() -> None:
    """Confere o Java do Spark e explica o que fazer se faltar.

    Usa `JAVA_HOME` (ou o `java` do PATH). Configuração documentada e testada:
    Java 17. Outra versão segue com um aviso — ela não é suportada aqui.
    """
    os.environ.setdefault("PYSPARK_PYTHON", sys.executable)
    os.environ.setdefault("PYSPARK_DRIVER_PYTHON", sys.executable)
    java_home = os.environ.get("JAVA_HOME")
    executavel = str(Path(java_home) / "bin" / "java") if java_home else shutil.which("java")
    if not executavel:
        sys.exit(
            "Java não encontrado. O Spark local precisa do Java 17 (por exemplo o Eclipse "
            "Temurin 17) com JAVA_HOME apontando para a pasta de instalação. Alternativa sem "
            "Java: rode o notebook no Databricks e importe o pacote com "
            "ferramentas/importar_processados.py."
        )
    versao = _versao_java(executavel)
    if versao is None:
        sys.exit(f"Não foi possível executar {executavel} -version. Confira o JAVA_HOME.")
    if versao != JAVA_SUPORTADO:
        print(f"AVISO: Java {versao} detectado. A configuração suportada é PySpark 3.5 + Java "
              f"{JAVA_SUPORTADO}; com outra versão o Spark pode falhar.")


def hadoop_nativo_disponivel() -> bool:
    return os.name != "nt" or bool(os.environ.get("HADOOP_HOME"))


def gravar_camada(df, pasta: Path, ts) -> str:
    """Grava uma cópia de inspeção da camada. Devolve o formato usado."""
    if hadoop_nativo_disponivel():
        df.write.mode("overwrite").parquet(str(pasta))
        return "parquet"
    pasta.mkdir(parents=True, exist_ok=True)
    with open(pasta / "parte-00000.jsonl", "w", encoding="utf-8", newline="\n") as arquivo:
        for linha in ts.para_linhas_json(df):
            arquivo.write(linha + "\n")
    return "jsonl"


def main() -> None:
    analisador = argparse.ArgumentParser(description="Pipeline local da Fase 2 do AgroSmart.")
    analisador.add_argument("--origem", choices=["real", "simulado", "ambos"], default="ambos",
                            help="Fonte do snapshot criado quando --snapshot não é informado.")
    analisador.add_argument("--snapshot", type=Path, help="Pasta de um snapshot RAW existente.")
    analisador.add_argument("--sem-camadas", action="store_true",
                            help="Não grava cópias de Bronze/Silver/Gold em dados/.")
    argumentos = analisador.parse_args()

    # 1. RAW -------------------------------------------------------------------
    if argumentos.snapshot:
        pasta_snapshot = argumentos.snapshot
    else:
        import exportar_snapshot

        if argumentos.origem in ("simulado", "ambos") and not c.ARQUIVO_SIMULADOS.exists():
            import gerar_dados_simulados

            print("Dados simulados ausentes; gerando com a semente padrão...")
            registros = gerar_dados_simulados.gerar()
            c.PASTA_SIMULADOS.mkdir(parents=True, exist_ok=True)
            with open(c.ARQUIVO_SIMULADOS, "w", encoding="utf-8", newline="\n") as arquivo:
                arquivo.writelines(json.dumps(r, ensure_ascii=False) + "\n" for r in registros)
        pasta_snapshot = exportar_snapshot.exportar(argumentos.origem)
    manifesto_raw = ingestao.validar_snapshot(pasta_snapshot)
    print(f"[RAW]    {manifesto_raw['id_snapshot']}: {manifesto_raw['quantidade_registros']} laudos "
          f"{manifesto_raw['registros_por_origem']}")

    # 2. Spark -----------------------------------------------------------------
    preparar_java()
    from src.pipeline import transformacoes_spark as ts

    spark = ts.criar_sessao_local()
    spark.sparkContext.setLogLevel("ERROR")
    id_execucao = ingestao.novo_id("exec")
    # Definido aqui, e não lido da Bronze: com RAW vazio não há linha de onde ler.
    processado_em = ts.agora_utc()
    try:
        camadas = ts.executar(spark, str(Path(pasta_snapshot) / "laudos.jsonl"), id_execucao, processado_em)
        contagens = {nome: df.count() for nome, df in camadas.items()}
        print(f"[BRONZE] {contagens['bronze']} registros")
        print(f"[SILVER] {contagens['silver']} válidos · {contagens['silver_rejeitados']} rejeitados")
        print(f"[GOLD]   {contagens['gold_analises']} análises · {contagens['gold_laudos']} laudos · "
              f"{contagens['gold_agregados']} agregados")

        if not argumentos.sem_camadas:
            formatos = set()
            for nome, df in camadas.items():
                camada = nome.split("_")[0]
                pasta = {"bronze": c.PASTA_BRONZE, "silver": c.PASTA_SILVER, "gold": c.PASTA_GOLD}[camada]
                formatos.add(gravar_camada(df, pasta / id_execucao / nome, ts))
            print(f"         camadas gravadas em dados/{{bronze,silver,gold}}/{id_execucao} ({', '.join(formatos)})")

        # 3. Pacote processado -------------------------------------------------
        pasta_pacote = ts.exportar_pacote(
            camadas["gold_analises"], camadas["gold_laudos"], camadas["gold_agregados"],
            camadas["gold_qualidade"], camadas["silver_rejeitados"],
            id_execucao=id_execucao,
            processado_em=processado_em,
            metadados={
                "ambiente": "spark-local",
                "versao_spark": spark.version,
                "id_snapshot": manifesto_raw["id_snapshot"],
                "snapshot_extraido_em": manifesto_raw["extraido_em"],
                "registros_raw_por_origem": manifesto_raw["registros_por_origem"],
                "contagens": contagens,
            },
            destino=c.PASTA_PROCESSADOS,
        )
    finally:
        spark.stop()

    print(f"[PACOTE] {pasta_pacote} (marcado como atual)")
    print("Recarregue http://localhost:8000/dashboard para ver os dados novos.")


if __name__ == "__main__":
    main()
