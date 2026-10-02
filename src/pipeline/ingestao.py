"""Fronteira RAW: extrai laudos das fontes e grava um snapshot imutável.

Duas fontes alimentam o pipeline:

* **real** — o histórico SQLite da Fase 1 (`dados/historico.db`), aberto em modo
  somente leitura. Só colunas de texto e número são lidas: a miniatura (BLOB)
  nunca sai do banco.
* **simulado** — o arquivo gerado por `ferramentas/gerar_dados_simulados.py`,
  que já segue o contrato e entra direto aqui, sem passar pelo SQLite.

Snapshot:

    dados/raw/<id_snapshot>/
        laudos.jsonl     um laudo por linha, campos de `contratos.CAMPOS_RAW`
        manifesto.json   versão, origem, contagens e hash do laudos.jsonl

O manifesto é gravado por último e a pasta só ganha o nome definitivo depois
dele: um snapshot interrompido no meio nunca parece completo.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from . import contratos as c

# Identifica a instalação que gerou os laudos reais e entra no `id_laudo`, para
# que históricos de duas máquinas não colidam. Não usa o nome do computador de
# propósito: o pacote processado pode ser compartilhado.
INSTANCIA_PADRAO = os.environ.get("AGROSMART_INSTANCIA") or "agrosmart-local"

COLUNAS_SQLITE = (
    "id", "arquivo", "data_hora", "motor", "especie", "diagnostico", "condicao",
    "confianca", "confianca_texto", "agente", "sintomas", "manejo",
    "observacoes", "modelo_versao",
)


class SnapshotInvalidoError(RuntimeError):
    """Snapshot RAW incompleto ou adulterado."""


def agora_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def novo_id(prefixo: str) -> str:
    instante = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{prefixo}_{instante}_{secrets.token_hex(3)}"


def sha256(caminho: Path) -> str:
    resumo = hashlib.sha256()
    with open(caminho, "rb") as arquivo:
        for bloco in iter(lambda: arquivo.read(1 << 16), b""):
            resumo.update(bloco)
    return resumo.hexdigest()


# --------------------------------------------------------------------------- #
# Fonte real: histórico SQLite da Fase 1
# --------------------------------------------------------------------------- #
def laudo_legado(linha: dict, instancia: str = INSTANCIA_PADRAO) -> dict:
    """Converte uma linha da tabela `laudos` da Fase 1 para o contrato.

    Se o laudo tiver vínculo em `analise_laudos` (envios feitos pela interface
    a partir da Fase 2), a linha traz também `id_analise`, `id_imagem`, modo,
    limiar e localidade da tabela `analises`. Sem vínculo — laudos antigos ou
    gravados por outro caminho — esses campos ficam nulos, inclusive
    `id_analise`: duas linhas seguidas da mesma foto no modo comparação **não**
    são agrupadas por suposição.
    """
    registro = c.registro_vazio()
    registro.update(
        versao_schema=c.VERSAO_SCHEMA,
        origem_instancia=instancia,
        id_laudo=f"{instancia}:laudo:{linha['id']}",
        arquivo=linha["arquivo"],
        data_analise=linha["data_hora"],
        origem_dado=c.ORIGEM_REAL,
        especie_original=linha["especie"],
        condicao=linha["condicao"],
        diagnostico_original=linha["diagnostico"],
        agente_original=linha["agente"],
        motor=linha["motor"],
        modelo_versao=linha["modelo_versao"],
        confianca=linha["confianca"],
        confianca_texto=linha["confianca_texto"],
        sintomas=linha["sintomas"],
        manejo=linha["manejo"],
        observacoes=linha["observacoes"],
    )
    if linha.get("id_analise"):
        registro.update(
            id_analise=linha["id_analise"],
            id_imagem=linha["id_imagem"],
            modo_solicitado=linha["modo"],
            limiar_cnn=linha["limiar"],
            uf=linha["uf"],
            municipio=linha["municipio"],
            propriedade=linha["propriedade"],
            talhao=linha["talhao"],
        )
    return registro


def ler_historico_sqlite(caminho: Path, instancia: str = INSTANCIA_PADRAO) -> list[dict]:
    """Laudos do histórico real. Banco ausente ou sem tabela -> lista vazia.

    Abre com `mode=ro`: a extração nunca cria, migra nem altera o banco
    operacional.
    """
    caminho = Path(caminho)
    if not caminho.exists():
        return []

    uri = f"file:{caminho.resolve().as_posix()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as conexao:
        conexao.row_factory = sqlite3.Row
        existe = conexao.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='laudos'"
        ).fetchone()
        if not existe:
            return []
        colunas = ", ".join(f"l.{nome}" for nome in COLUNAS_SQLITE)
        tem_analises = conexao.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' "
            "AND name IN ('analises', 'analise_laudos')"
        ).fetchone()[0] == 2
        if tem_analises:
            consulta = f"""
                SELECT {colunas}, a.id_analise, a.id_imagem, a.modo, a.limiar,
                       a.uf, a.municipio, a.propriedade, a.talhao
                FROM laudos l
                LEFT JOIN analise_laudos v ON v.id_laudo = l.id
                LEFT JOIN analises a ON a.id_analise = v.id_analise
                ORDER BY l.id
            """
        else:  # banco criado antes da Fase 2 e ainda não aberto pelo app novo
            consulta = f"SELECT {colunas} FROM laudos l ORDER BY l.id"
        linhas = conexao.execute(consulta).fetchall()
    return [laudo_legado(dict(linha), instancia) for linha in linhas]


# --------------------------------------------------------------------------- #
# Fonte simulada
# --------------------------------------------------------------------------- #
def ler_simulados(caminho: Path = c.ARQUIVO_SIMULADOS) -> list[dict]:
    """Laudos do gerador. Recusa qualquer linha que não se declare simulada."""
    caminho = Path(caminho)
    if not caminho.exists():
        raise FileNotFoundError(
            f"{caminho} não existe. Gere os dados com: "
            ".venv\\Scripts\\python.exe ferramentas\\gerar_dados_simulados.py"
        )
    registros = []
    with open(caminho, encoding="utf-8") as arquivo:
        for numero, linha in enumerate(arquivo, start=1):
            if not linha.strip():
                continue
            registro = json.loads(linha)
            if registro.get("origem_dado") != c.ORIGEM_SIMULADO:
                raise ValueError(
                    f"{caminho.name}, linha {numero}: origem_dado deveria ser "
                    f"'{c.ORIGEM_SIMULADO}', veio {registro.get('origem_dado')!r}."
                )
            registros.append(registro)
    return registros


# --------------------------------------------------------------------------- #
# Snapshot RAW
# --------------------------------------------------------------------------- #
def _no_contrato(registro: dict, id_snapshot: str, extraido_em: str) -> dict:
    """Só os campos do contrato, na ordem do contrato, todos presentes."""
    saida = {nome: registro.get(nome) for nome, _ in c.CAMPOS_LAUDO}
    saida["id_snapshot"] = id_snapshot
    saida["extraido_em"] = extraido_em
    return saida


def criar_snapshot(
    registros: Iterable[dict],
    fontes: list[dict],
    destino: Path = c.PASTA_RAW,
    id_snapshot: str | None = None,
) -> Path:
    """Grava `laudos.jsonl` + `manifesto.json` e devolve a pasta do snapshot.

    `fontes` descreve de onde vieram os registros (tipo, caminho, quantidade)
    e vai para o manifesto, para que o snapshot seja auditável sozinho.
    """
    id_snapshot = id_snapshot or novo_id("snap")
    extraido_em = agora_utc()
    destino = Path(destino)
    final = destino / id_snapshot
    temporaria = destino / f".tmp_{id_snapshot}"
    if final.exists():
        raise FileExistsError(f"Snapshot {id_snapshot} já existe.")
    temporaria.mkdir(parents=True, exist_ok=False)

    origens: Counter = Counter()
    total = 0
    with open(temporaria / "laudos.jsonl", "w", encoding="utf-8", newline="\n") as arquivo:
        for registro in registros:
            linha = _no_contrato(registro, id_snapshot, extraido_em)
            origens[str(linha["origem_dado"])] += 1
            total += 1
            arquivo.write(json.dumps(linha, ensure_ascii=False) + "\n")

    manifesto = {
        "tipo": "snapshot_raw",
        "versao_schema": c.VERSAO_SCHEMA,
        "id_snapshot": id_snapshot,
        "extraido_em": extraido_em,
        "quantidade_registros": total,
        "registros_por_origem": dict(sorted(origens.items())),
        "fontes": fontes,
        "arquivos": {"laudos.jsonl": {"sha256": sha256(temporaria / "laudos.jsonl"), "linhas": total}},
        "observacao": "Somente campos textuais e numéricos. Imagens, miniaturas e BLOBs não são extraídos.",
    }
    (temporaria / c.MANIFESTO).write_text(
        json.dumps(manifesto, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n"
    )
    temporaria.rename(final)
    return final


def validar_snapshot(pasta: Path) -> dict:
    """Confere manifesto e hash. Devolve o manifesto ou lança erro."""
    pasta = Path(pasta)
    caminho_manifesto = pasta / c.MANIFESTO
    if not caminho_manifesto.exists():
        raise SnapshotInvalidoError(f"{pasta} não tem {c.MANIFESTO}.")
    manifesto = json.loads(caminho_manifesto.read_text(encoding="utf-8"))
    esperado = manifesto["arquivos"]["laudos.jsonl"]["sha256"]
    if sha256(pasta / "laudos.jsonl") != esperado:
        raise SnapshotInvalidoError(f"laudos.jsonl de {pasta.name} não confere com o manifesto.")
    return manifesto


def ultimo_snapshot(destino: Path = c.PASTA_RAW) -> Path | None:
    """Snapshot completo mais recente (ignora pastas temporárias)."""
    destino = Path(destino)
    if not destino.exists():
        return None
    candidatos = sorted(
        p for p in destino.iterdir()
        if p.is_dir() and not p.name.startswith(".") and (p / c.MANIFESTO).exists()
    )
    return candidatos[-1] if candidatos else None
