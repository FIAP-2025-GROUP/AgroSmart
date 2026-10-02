"""Pacote processado: a única ponte entre o pipeline e a API.

    dados/processados/<id_execucao>/
        analises.jsonl    gold_analises
        laudos.jsonl      gold_laudos
        agregados.jsonl   gold_agregados
        qualidade.json    gold_qualidade + amostra de rejeitados
        manifesto.json    contagens, hash e status — gravado por último
    dados/processados/ATUAL.json   aponta a execução que a API deve servir

Garantias:

* Os arquivos são escritos numa pasta temporária exclusiva desta publicação, o
  manifesto (com o SHA-256 e a quantidade de linhas de cada arquivo) é o último
  a ser gravado, e só então a pasta é renomeada para o nome definitivo. Uma
  execução que falhou no meio nunca aparece como pacote.
* O ponteiro `ATUAL.json` é trocado com `os.replace`, que é atômico: a API lê o
  ponteiro antigo ou o novo, nunca um meio-termo.
* `id_execucao` só aceita o formato gerado pelo sistema (`exec_` + letras,
  dígitos, `_` e `-`) e o caminho resolvido precisa ficar dentro da pasta de
  processados: nada de `..`, barras ou caminhos absolutos.
* A validação é completa e **nunca** deixa escapar uma exceção genérica: tudo o
  que vier errado de um arquivo externo vira `PacoteInvalidoError`. Ela confere
  o manifesto (tipos, status, schema, id), cada arquivo (presença, hash,
  linhas declaradas), a estrutura mínima de cada registro, a unicidade das
  identidades e as contagens declaradas.
* A API lê os quatro arquivos da mesma pasta, já validados nesta mesma leitura —
  nunca mistura execuções.

Módulo puro (sem Spark): serve ao pipeline local, ao notebook do Databricks, à
API e ao `importar_processados.py`.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from . import contratos as c
from .ingestao import agora_utc, sha256

PADRAO_ID_EXECUCAO = re.compile(r"^exec_[A-Za-z0-9][A-Za-z0-9_-]{0,79}$")
PADRAO_SHA256 = re.compile(r"^[0-9a-f]{64}$")
ARQUIVOS_JSONL = ("analises.jsonl", "laudos.jsonl", "agregados.jsonl")


class PacoteInvalidoError(RuntimeError):
    """Pacote incompleto, adulterado, malformado ou de schema incompatível."""


class ColisaoPacoteError(PacoteInvalidoError):
    """Já existe uma execução com o mesmo id e conteúdo diferente."""


# --------------------------------------------------------------------------- #
# Caminhos
# --------------------------------------------------------------------------- #
def validar_id_execucao(id_execucao: Any) -> str:
    if not isinstance(id_execucao, str) or not PADRAO_ID_EXECUCAO.match(id_execucao):
        raise PacoteInvalidoError(f"id_execucao inválido: {id_execucao!r}")
    return id_execucao


def pasta_execucao(destino: Path, id_execucao: Any) -> Path:
    """Caminho da execução, garantidamente dentro de `destino`."""
    validar_id_execucao(id_execucao)
    base = Path(destino).resolve()
    pasta = (base / id_execucao).resolve()
    if pasta.parent != base:
        raise PacoteInvalidoError(f"id_execucao escapa da pasta de processados: {id_execucao!r}")
    return pasta


def _temporario(destino: Path, prefixo: str) -> Path:
    """Nome temporário exclusivo desta operação (duas publicações não se pisam)."""
    return Path(destino) / f".{prefixo}_{secrets.token_hex(4)}"


# --------------------------------------------------------------------------- #
# Publicação
# --------------------------------------------------------------------------- #
def _escrever_linhas(caminho: Path, linhas: Iterable[str]) -> int:
    total = 0
    with open(caminho, "w", encoding="utf-8", newline="\n") as arquivo:
        for linha in linhas:
            arquivo.write(linha.rstrip("\n") + "\n")
            total += 1
    return total


def publicar(
    id_execucao: str,
    analises: Iterable[str],
    laudos: Iterable[str],
    agregados: Iterable[str],
    qualidade: dict,
    metadados: dict,
    destino: Path = None,
    marcar_como_atual: bool = True,
) -> Path:
    """Grava o pacote de forma atômica, valida e devolve a pasta final.

    `analises`, `laudos` e `agregados` são linhas JSON já serializadas.
    `metadados` entra no manifesto (snapshot de origem, ambiente, contagens).
    Se o resultado não passar na validação, a pasta temporária é apagada e nada
    é publicado.
    """
    destino = Path(destino or c.PASTA_PROCESSADOS)
    destino.mkdir(parents=True, exist_ok=True)
    final = pasta_execucao(destino, id_execucao)
    if final.exists():
        raise FileExistsError(f"A execução {id_execucao} já foi publicada.")
    temporaria = _temporario(destino, f"tmp_{id_execucao}")
    temporaria.mkdir()

    try:
        linhas = {
            "analises.jsonl": _escrever_linhas(temporaria / "analises.jsonl", analises),
            "laudos.jsonl": _escrever_linhas(temporaria / "laudos.jsonl", laudos),
            "agregados.jsonl": _escrever_linhas(temporaria / "agregados.jsonl", agregados),
        }
        (temporaria / "qualidade.json").write_text(
            json.dumps(qualidade, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")

        manifesto = {
            "tipo": "pacote_processado",
            "versao_schema": c.VERSAO_SCHEMA,
            "versao_normalizacao": c.VERSAO_NORMALIZACAO,
            "id_execucao": id_execucao,
            "publicado_em": agora_utc(),
            **metadados,
            "arquivos": {
                nome: {"sha256": sha256(temporaria / nome), "linhas": linhas.get(nome)}
                for nome in c.ARQUIVOS_PACOTE
            },
            "status": c.STATUS_COMPLETO,
        }
        (temporaria / c.MANIFESTO).write_text(
            json.dumps(manifesto, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
        ler(temporaria, nome_esperado=id_execucao)  # nunca publica o que a API recusaria
        temporaria.rename(final)
    except BaseException:
        shutil.rmtree(temporaria, ignore_errors=True)
        raise

    if marcar_como_atual:
        marcar_atual(id_execucao, destino)
    return final


def marcar_atual(id_execucao: str, destino: Path = None) -> None:
    """Aponta a API para uma execução já publicada e válida."""
    destino = Path(destino or c.PASTA_PROCESSADOS)
    validar(pasta_execucao(destino, id_execucao))
    temporario = _temporario(destino, "ATUAL").with_suffix(".tmp")
    temporario.write_text(json.dumps({"id_execucao": id_execucao, "marcado_em": agora_utc()}),
                          encoding="utf-8", newline="\n")
    os.replace(temporario, destino / c.PONTEIRO_ATUAL)


# --------------------------------------------------------------------------- #
# Validação
# --------------------------------------------------------------------------- #
@dataclass
class PacoteLido:
    pasta: Path
    manifesto: dict
    analises: list[dict]
    laudos: list[dict]
    agregados: list[dict]
    qualidade: dict


def _erro(pasta: Path, mensagem: str) -> PacoteInvalidoError:
    return PacoteInvalidoError(f"{pasta.name}: {mensagem}")


def _texto_ou_nulo(valor: Any) -> bool:
    return valor is None or isinstance(valor, str)


def _inteiro(valor: Any) -> bool:
    return isinstance(valor, int) and not isinstance(valor, bool) and valor >= 0


# Estrutura mínima de cada registro: o que a API usa sem checar de novo.
_DATA = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _checar_comum(r: dict) -> str | None:
    if r.get("origem_dado") not in c.ORIGENS:
        return "origem_dado inválida"
    if r.get("condicao") not in c.CONDICOES:
        return "condicao inválida"
    if not isinstance(r.get("data_dia"), str) or not _DATA.match(r["data_dia"]):
        return "data_dia ausente ou fora do formato AAAA-MM-DD"
    if not isinstance(r.get("data_analise_ts"), str):
        return "data_analise_ts ausente"
    if r.get("categoria_problema") not in c.CATEGORIAS:
        return "categoria_problema inválida"
    for campo in ("origem_instancia", "uf", "municipio", "propriedade", "talhao", "cultura",
                  "id_imagem", "diagnostico_normalizado", "diagnostico_original", "modo_solicitado"):
        if not _texto_ou_nulo(r.get(campo)):
            return f"{campo} deveria ser texto"
    return None


def _checar_analise(r: dict) -> str | None:
    if not isinstance(r.get("id_analise"), str) or not r["id_analise"]:
        return "id_analise ausente"
    if not isinstance(r.get("semana"), str) or not _DATA.match(r["semana"]):
        return "semana ausente"
    if r.get("motor_principal") not in c.MOTORES:
        return "motor_principal inválido"
    for campo in ("comparacao",):
        if not isinstance(r.get(campo), bool):
            return f"{campo} deveria ser booleano"
    for campo in ("e_reanalise", "concordancia_motores"):
        if r.get(campo) is not None and not isinstance(r.get(campo), bool):
            return f"{campo} deveria ser booleano ou nulo"
    return _checar_comum(r)


def _checar_laudo(r: dict) -> str | None:
    if not isinstance(r.get("id_laudo"), str) or not r["id_laudo"]:
        return "id_laudo ausente"
    if not _texto_ou_nulo(r.get("id_analise")):
        return "id_analise deveria ser texto ou nulo"
    if r.get("motor") not in c.MOTORES:
        return "motor inválido"
    confianca = r.get("confianca")
    if confianca is not None and (isinstance(confianca, bool) or not isinstance(confianca, (int, float))
                                  or not 0 <= confianca <= 1):
        return "confianca fora de [0, 1]"
    if r.get("motor") == c.MOTOR_VLM and confianca is not None:
        return "confianca numérica em laudo do VLM"
    return _checar_comum(r)


def _checar_agregado(r: dict) -> str | None:
    if not isinstance(r.get("semana"), str) or not _DATA.match(r["semana"]):
        return "semana ausente"
    if r.get("origem_dado") not in c.ORIGENS:
        return "origem_dado inválida"
    if not _inteiro(r.get("n_analises")):
        return "n_analises deveria ser inteiro"
    soma = 0
    for condicao in c.CONDICOES:
        if not _inteiro(r.get(f"n_{condicao}", 0)):
            return f"n_{condicao} deveria ser inteiro"
        soma += r.get(f"n_{condicao}", 0)
    if "n_saudavel" in r and soma != r["n_analises"]:
        return "condições não somam n_analises"
    return None


_CHECAGENS = {"analises.jsonl": _checar_analise, "laudos.jsonl": _checar_laudo,
              "agregados.jsonl": _checar_agregado}


def _ler_jsonl(pasta: Path, nome: str) -> list[dict]:
    registros = []
    checar = _CHECAGENS[nome]
    with open(pasta / nome, encoding="utf-8") as arquivo:
        for numero, linha in enumerate(arquivo, start=1):
            if not linha.strip():
                raise _erro(pasta, f"{nome}, linha {numero}: linha vazia.")
            try:
                registro = json.loads(linha)
            except json.JSONDecodeError as erro:
                raise _erro(pasta, f"{nome}, linha {numero}: JSON inválido ({erro.msg}).") from None
            if not isinstance(registro, dict):
                raise _erro(pasta, f"{nome}, linha {numero}: registro deveria ser objeto.")
            problema = checar(registro)
            if problema:
                raise _erro(pasta, f"{nome}, linha {numero}: {problema}.")
            registros.append(registro)
    return registros


def _checar_manifesto(pasta: Path, manifesto: Any, nome_esperado: str) -> dict:
    if not isinstance(manifesto, dict):
        raise _erro(pasta, "manifesto deveria ser um objeto JSON.")
    if manifesto.get("tipo", "pacote_processado") != "pacote_processado":
        raise _erro(pasta, f"tipo {manifesto.get('tipo')!r}, esperado 'pacote_processado'.")
    if manifesto.get("status") != c.STATUS_COMPLETO:
        raise _erro(pasta, f"status {manifesto.get('status')!r}, esperado 'completo'.")
    versao = manifesto.get("versao_schema")
    if not isinstance(versao, str) or versao.split(".")[0] != c.VERSAO_SCHEMA.split(".")[0]:
        raise _erro(pasta, f"schema {versao!r} incompatível com {c.VERSAO_SCHEMA}.")
    id_execucao = manifesto.get("id_execucao")
    try:
        validar_id_execucao(id_execucao)
    except PacoteInvalidoError as erro:
        raise _erro(pasta, str(erro)) from None
    if id_execucao != nome_esperado:
        raise _erro(pasta, f"manifesto pertence à execução {id_execucao!r}.")

    arquivos = manifesto.get("arquivos")
    if not isinstance(arquivos, dict):
        raise _erro(pasta, "'arquivos' deveria ser um objeto.")
    for nome in c.ARQUIVOS_PACOTE:
        entrada = arquivos.get(nome)
        if not isinstance(entrada, dict):
            raise _erro(pasta, f"manifesto não descreve {nome}.")
        if not isinstance(entrada.get("sha256"), str) or not PADRAO_SHA256.match(entrada["sha256"]):
            raise _erro(pasta, f"hash de {nome} ausente ou malformado.")
        if entrada.get("linhas") is not None and not _inteiro(entrada["linhas"]):
            raise _erro(pasta, f"linhas de {nome} deveria ser inteiro.")
    for campo in ("por_origem", "contagens"):
        if campo in manifesto and not isinstance(manifesto[campo], dict):
            raise _erro(pasta, f"'{campo}' deveria ser um objeto.")
    return manifesto


def _checar_contagens(pasta: Path, manifesto: dict, lido: PacoteLido) -> None:
    reais = {"analises.jsonl": len(lido.analises), "laudos.jsonl": len(lido.laudos),
             "agregados.jsonl": len(lido.agregados)}
    for nome, quantidade in reais.items():
        declarada = manifesto["arquivos"][nome].get("linhas")
        if declarada is not None and declarada != quantidade:
            raise _erro(pasta, f"{nome} tem {quantidade} linhas, manifesto declara {declarada}.")

    contagens = manifesto.get("contagens", {})
    for chave, quantidade in (("gold_analises", len(lido.analises)), ("gold_laudos", len(lido.laudos)),
                              ("gold_agregados", len(lido.agregados))):
        if chave in contagens and contagens[chave] != quantidade:
            raise _erro(pasta, f"contagens.{chave} = {contagens[chave]!r}, arquivo tem {quantidade}.")

    for origem, declarado in manifesto.get("por_origem", {}).items():
        if not isinstance(declarado, dict):
            raise _erro(pasta, f"por_origem.{origem} deveria ser um objeto.")
        for unidade, registros in (("analises", lido.analises), ("laudos", lido.laudos)):
            real = sum(1 for r in registros if r["origem_dado"] == origem)
            if declarado.get(unidade, 0) != real:
                raise _erro(pasta, f"por_origem.{origem}.{unidade} = {declarado.get(unidade)!r}, "
                                   f"arquivo tem {real}.")

    if sum(a["n_analises"] for a in lido.agregados) != len(lido.analises):
        raise _erro(pasta, "agregados não somam o total de análises.")

    # Identidade composta: a mesma origem não pode repetir análise nem laudo.
    def chave(r, campo):
        return (r["origem_dado"], r.get("origem_instancia"), r[campo])

    for registros, campo in ((lido.analises, "id_analise"), (lido.laudos, "id_laudo")):
        vistas = set()
        for r in registros:
            k = chave(r, campo)
            if k in vistas:
                raise _erro(pasta, f"{campo} repetido na mesma origem: {r[campo]!r}.")
            vistas.add(k)


def _checar_qualidade(pasta: Path, qualidade: Any) -> dict:
    if not isinstance(qualidade, dict):
        raise _erro(pasta, "qualidade.json deveria ser um objeto.")
    metricas = qualidade.get("metricas", [])
    amostra = qualidade.get("amostra_rejeitados", [])
    if not isinstance(metricas, list) or not isinstance(amostra, list):
        raise _erro(pasta, "qualidade.json: 'metricas' e 'amostra_rejeitados' deveriam ser listas.")
    for m in metricas:
        if not (isinstance(m, dict) and isinstance(m.get("origem_dado"), str)
                and isinstance(m.get("metrica"), str) and _inteiro(m.get("valor"))
                and _inteiro(m.get("denominador"))):
            raise _erro(pasta, f"qualidade.json: métrica malformada {m!r:.120}.")
    if not all(isinstance(r, dict) for r in amostra):
        raise _erro(pasta, "qualidade.json: amostra de rejeitados malformada.")
    return qualidade


def ler(pasta: Path, nome_esperado: str | None = None) -> PacoteLido:
    """Valida tudo e devolve o conteúdo. Qualquer defeito vira `PacoteInvalidoError`."""
    pasta = Path(pasta)
    try:
        caminho = pasta / c.MANIFESTO
        if not caminho.is_file():
            raise _erro(pasta, f"sem {c.MANIFESTO}.")
        try:
            manifesto = json.loads(caminho.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as erro:
            raise _erro(pasta, f"manifesto ilegível ({erro}).") from None
        manifesto = _checar_manifesto(pasta, manifesto, nome_esperado or pasta.name)

        for nome in c.ARQUIVOS_PACOTE:
            arquivo = pasta / nome
            if not arquivo.is_file():
                raise _erro(pasta, f"falta {nome}.")
            if sha256(arquivo) != manifesto["arquivos"][nome]["sha256"]:
                raise _erro(pasta, f"{nome} não confere com o hash do manifesto.")

        try:
            qualidade = json.loads((pasta / "qualidade.json").read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as erro:
            raise _erro(pasta, f"qualidade.json ilegível ({erro}).") from None

        lido = PacoteLido(
            pasta=pasta,
            manifesto=manifesto,
            analises=_ler_jsonl(pasta, "analises.jsonl"),
            laudos=_ler_jsonl(pasta, "laudos.jsonl"),
            agregados=_ler_jsonl(pasta, "agregados.jsonl"),
            qualidade=_checar_qualidade(pasta, qualidade),
        )
        _checar_contagens(pasta, manifesto, lido)
        return lido
    except PacoteInvalidoError:
        raise
    except (OSError, UnicodeDecodeError, ValueError, TypeError, KeyError, AttributeError) as erro:
        # Último anteparo: um arquivo externo nunca derruba quem chama.
        raise _erro(pasta, f"pacote malformado ({type(erro).__name__}: {erro}).") from None


def validar(pasta: Path) -> dict:
    """Confere o pacote inteiro e devolve o manifesto."""
    return ler(pasta).manifesto


def mesmo_conteudo(pasta_a: Path, pasta_b: Path) -> bool:
    """Dois pacotes válidos são idênticos se os manifestos (que trazem o hash de
    cada arquivo) forem byte a byte iguais."""
    return sha256(Path(pasta_a) / c.MANIFESTO) == sha256(Path(pasta_b) / c.MANIFESTO)


# --------------------------------------------------------------------------- #
# Pacote atual
# --------------------------------------------------------------------------- #
def _candidatos(destino: Path) -> list[Path]:
    if not destino.is_dir():
        return []
    return sorted((p for p in destino.iterdir()
                   if p.is_dir() and PADRAO_ID_EXECUCAO.match(p.name)), reverse=True)


def assinatura(destino: Path = None) -> tuple:
    """Muda sempre que o ponteiro ou qualquer arquivo de qualquer pacote muda.

    Barata (só `stat`): a API recarrega o pacote quando ela muda.
    """
    destino = Path(destino or c.PASTA_PROCESSADOS)
    partes = []
    ponteiro = destino / c.PONTEIRO_ATUAL
    if ponteiro.is_file():
        estado = ponteiro.stat()
        partes.append(("ATUAL", estado.st_mtime_ns, estado.st_size))
    for pasta in _candidatos(destino):
        for nome in (c.MANIFESTO, *c.ARQUIVOS_PACOTE):
            arquivo = pasta / nome
            if arquivo.is_file():
                estado = arquivo.stat()
                partes.append((pasta.name, nome, estado.st_mtime_ns, estado.st_size))
    return tuple(partes)


def abrir_atual(destino: Path = None) -> tuple[PacoteLido | None, str | None]:
    """(pacote a servir, aviso).

    Usa o ponteiro `ATUAL.json`. Se ele faltar ou apontar para um pacote
    inválido, cai para a execução **válida** mais recente e devolve um aviso que
    o dashboard exibe. Nunca devolve um pacote que não passou na validação.
    """
    destino = Path(destino or c.PASTA_PROCESSADOS)
    aviso = None
    ponteiro = destino / c.PONTEIRO_ATUAL
    if ponteiro.is_file():
        try:
            conteudo = json.loads(ponteiro.read_text(encoding="utf-8"))
            if not isinstance(conteudo, dict):
                raise PacoteInvalidoError("ATUAL.json deveria ser um objeto.")
            return ler(pasta_execucao(destino, conteudo.get("id_execucao"))), None
        except (PacoteInvalidoError, json.JSONDecodeError, UnicodeDecodeError, OSError) as erro:
            aviso = f"O pacote marcado como atual foi ignorado: {erro}"

    for pasta in _candidatos(destino):
        try:
            return ler(pasta), aviso or "Sem ponteiro ATUAL.json; usando a execução válida mais recente."
        except PacoteInvalidoError:
            continue
    return None, aviso


def localizar_atual(destino: Path = None) -> tuple[Path | None, str | None]:
    lido, aviso = abrir_atual(destino)
    return (lido.pasta if lido else None), aviso
