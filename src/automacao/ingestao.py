"""Ingestão incremental da pasta monitorada (micro-batch com checkpoint).

Para cada arquivo de `dados/entrada/` o banco guarda quantos **bytes** e
**linhas** já foram consumidos e o SHA-256 do trecho lido. A cada ciclo:

1. arquivo novo → lido desde o começo;
2. arquivo que cresceu (linhas acrescentadas no fim) → lido só a partir do
   checkpoint;
3. arquivo do mesmo tamanho e mesmo trecho inicial → ignorado, nada a fazer;
4. arquivo que encolheu ou cujo começo mudou (foi reescrito) → relido desde o
   começo; os registros que já existiam são descartados como duplicados pela
   chave, então nenhuma ação dispara de novo.

Só linhas **completas** (terminadas em `\\n`) são consumidas: se o produtor
ainda estiver escrevendo a última linha, ela fica para o próximo ciclo.

Além do checkpoint por arquivo, cada registro tem uma chave única
(`id_laudo` com a origem; `id_leitura`). O mesmo dado entregue de novo — outro
arquivo com o mesmo conteúdo, por exemplo — é contado como duplicado e não
gera ação. Registros e checkpoint são gravados na mesma transação.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..pipeline import contratos as c2
from ..pipeline import normalizacao as n
from . import armazenamento as db
from . import contratos as c

FUSO_BRASILIA = timezone(timedelta(hours=-3))
_CONTROLE = re.compile(r"[\x00-\x1f\x7f]")
BYTES_ASSINATURA = 64 * 1024


def agora() -> str:
    return datetime.now(FUSO_BRASILIA).isoformat(timespec="seconds")


def instante(texto) -> datetime | None:
    """ISO-8601 → datetime em Brasília. Sem fuso = horário de Brasília."""
    if not texto or not isinstance(texto, str):
        return None
    try:
        valor = datetime.fromisoformat(texto.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if valor.tzinfo is None:
        valor = valor.replace(tzinfo=FUSO_BRASILIA)
    return valor.astimezone(FUSO_BRASILIA)


def _assinatura(caminho: Path, ate: int) -> str:
    with open(caminho, "rb") as arquivo:
        return hashlib.sha256(arquivo.read(min(ate, BYTES_ASSINATURA))).hexdigest()


# --------------------------------------------------------------------------- #
# Validação e normalização de um registro
# --------------------------------------------------------------------------- #
def normalizar_laudo(bruto: dict) -> tuple[dict | None, list[str]]:
    """Laudo da Fase 2 → linha da tabela `laudos` (ou motivos de rejeição)."""
    if not isinstance(bruto, dict):
        return None, ["registro_nao_e_objeto"]
    r = {k: (n.limpar(v) if isinstance(v, str) else v) for k, v in bruto.items()}
    quando = instante(r.get("data_analise"))
    condicao = n.normalizar_condicao(r.get("condicao"))
    motor = n.normalizar_motor(r.get("motor"))
    confianca = r.get("confianca")

    motivos = []
    if not r.get("id_laudo"):
        motivos.append("id_laudo_ausente")
    if quando is None:
        motivos.append("data_invalida" if r.get("data_analise") else "data_ausente")
    if r.get("origem_dado") not in c2.ORIGENS:
        motivos.append("origem_dado_invalida")
    if motor is None:
        motivos.append("motor_desconhecido")
    if condicao is None:
        motivos.append("condicao_invalida")
    if confianca is not None and (not isinstance(confianca, (int, float)) or not 0 <= confianca <= 1):
        motivos.append("confianca_fora_do_intervalo")
    if motivos:
        return None, motivos

    cultura, _ = n.normalizar_cultura(r.get("cultura"), r.get("especie_original"))
    diagnostico, categoria, _ = n.normalizar_diagnostico(
        condicao, r.get("diagnostico_original"), r.get("agente_original"))
    return {
        "chave": f"{r['origem_dado']}|{r.get('origem_instancia') or ''}|{r['id_laudo']}",
        "id_laudo": r["id_laudo"],
        "id_analise": r.get("id_analise"),
        "instante": quando.isoformat(timespec="seconds"),
        "dia": quando.date().isoformat(),
        "uf": r.get("uf"), "municipio": r.get("municipio"),
        "propriedade": r.get("propriedade"), "talhao": r.get("talhao"),
        "cultura": cultura, "condicao": condicao,
        "diagnostico": diagnostico, "categoria": categoria,
        "motor": motor, "confianca": confianca,
        "manejo": r.get("manejo"), "origem_dado": r["origem_dado"],
    }, []


def _numero(texto, decimal_virgula: bool):
    if isinstance(texto, (int, float)) and not isinstance(texto, bool):
        return float(texto)
    if not isinstance(texto, str) or not texto.strip():
        return None
    valor = texto.strip()
    if decimal_virgula and "," in valor:
        valor = valor.replace(".", "").replace(",", ".")  # "1.234,5" -> 1234.5; "1.5" fica como está
    try:
        return float(valor)
    except ValueError:
        return None


def normalizar_leitura(bruto: dict, decimal_virgula: bool = False) -> tuple[dict | None, list[str]]:
    """Leitura de sensor → linha da tabela `leituras` (ou motivos de rejeição)."""
    if not isinstance(bruto, dict):
        return None, ["registro_nao_e_objeto"]
    r = {k: (v.strip() if isinstance(v, str) else v) for k, v in bruto.items()}
    motivos = [f"{campo}_ausente" for campo in c.OBRIGATORIOS_SENSOR
               if r.get(campo) in (None, "")]
    quando = instante(r.get("instante"))
    if r.get("instante") and quando is None:
        motivos.append("instante_invalido")

    numeros = {}
    for campo, (minimo, maximo) in c.NUMERICOS_SENSOR.items():
        if r.get(campo) in (None, ""):
            continue
        valor = _numero(r[campo], decimal_virgula)
        if valor is None:
            motivos.append(f"{campo}_nao_numerico")
        elif not minimo <= valor <= maximo:
            motivos.append(f"{campo}_fora_da_faixa")
        else:
            numeros[campo] = valor
    if "pragas_armadilha" in numeros and not float(numeros["pragas_armadilha"]).is_integer():
        motivos.append("pragas_armadilha_nao_inteiro")
    cultura, _ = n.normalizar_cultura(r.get("cultura"), None)
    if r.get("cultura") not in (None, "") and cultura is None:
        motivos.append("cultura_ausente")  # "-", "NA", "null"... são marcadores de vazio
    for campo in ("id_leitura", "id_sensor", "propriedade", "talhao"):
        if isinstance(r.get(campo), str) and _CONTROLE.search(r[campo]):
            motivos.append(f"{campo}_com_caractere_de_controle")
    if motivos:
        return None, motivos

    return {
        "id_leitura": str(r["id_leitura"]),
        "instante": quando.isoformat(timespec="seconds"),
        "id_sensor": str(r["id_sensor"]),
        "uf": r.get("uf") or None, "municipio": r.get("municipio") or None,
        "propriedade": str(r["propriedade"]), "talhao": str(r["talhao"]),
        "cultura": cultura,
        **{campo: numeros[campo] for campo in c.NUMERICOS_SENSOR if campo != "pragas_armadilha"},
        "pragas_armadilha": int(numeros["pragas_armadilha"]),
    }, []


# --------------------------------------------------------------------------- #
# Leitura incremental de um arquivo
# --------------------------------------------------------------------------- #
@dataclass
class ResultadoArquivo:
    nome: str
    tipo: str
    linhas_novas: int = 0
    novos: int = 0
    duplicados: int = 0
    rejeitados: int = 0
    reiniciado: bool = False  # arquivo reescrito: relido desde o começo
    laudos: list[dict] = field(default_factory=list)
    leituras: list[dict] = field(default_factory=list)


def pendentes(conexao: sqlite3.Connection, ambiente: c.Ambiente) -> list[tuple[Path, str]]:
    """Arquivos da pasta monitorada com bytes ainda não consumidos."""
    if not ambiente.entrada.is_dir():
        return []
    checkpoints = {r["nome"]: r for r in db.linhas(conexao.execute("SELECT * FROM arquivos"))}
    candidatos = []
    for caminho in ambiente.entrada.iterdir():
        tipo = c.tipo_do_arquivo(caminho.name)
        try:
            if tipo is None or not caminho.is_file():
                continue
            candidatos.append((caminho.stat().st_mtime_ns, caminho.name, caminho, tipo))
        except OSError:
            continue  # renomeado/removido durante a varredura
    saida = []
    for _, _, caminho, tipo in sorted(candidatos, key=lambda x: (x[0], x[1])):
        try:
            ponto = checkpoints.get(caminho.name)
            tamanho = caminho.stat().st_size
            inicio = 0
            if ponto and tamanho >= ponto["bytes_lidos"] and \
                    _assinatura(caminho, ponto["bytes_lidos"]) == ponto["assinatura"]:
                inicio = ponto["bytes_lidos"]
                if tamanho == inicio:
                    continue  # nada novo
            with open(caminho, "rb") as arquivo:
                arquivo.seek(inicio)
                if b"\n" not in arquivo.read():
                    continue  # o produtor ainda está escrevendo a primeira linha nova
        except OSError:
            continue  # arquivo travado (ex.: aberto no Excel) ou sumiu: tenta no próximo ciclo
        saida.append((caminho, tipo))
    return saida


def _linhas(texto: str) -> list[str]:
    """Quebra só em \\n (splitlines também quebraria em U+2028 dentro de um campo JSON)."""
    return [linha.rstrip("\r") for linha in texto.split("\n")[:-1]]


def _registros_jsonl(texto: str):
    for linha in _linhas(texto):
        if not linha.strip():
            yield None, None
            continue
        try:
            yield json.loads(linha), linha
        except json.JSONDecodeError:
            yield "json_invalido", linha


def _registros_csv(texto: str, cabecalho: str):
    delimitador = ";" if cabecalho.count(";") > cabecalho.count(",") else ","
    nomes = next(csv.reader([cabecalho], delimiter=delimitador))
    for linha in _linhas(texto):
        if not linha.strip():
            yield None, None
            continue
        valores = next(csv.reader(io.StringIO(linha), delimiter=delimitador))
        if len(valores) != len(nomes):
            yield "quantidade_de_colunas_invalida", linha
            continue
        yield dict(zip(nomes, valores)), linha


def processar_arquivo(conexao: sqlite3.Connection, caminho: Path, tipo: str, id_lote: int) -> ResultadoArquivo:
    """Consome as linhas novas de um arquivo numa única transação."""
    resultado = ResultadoArquivo(nome=caminho.name, tipo=tipo)
    ponto = conexao.execute("SELECT * FROM arquivos WHERE nome = ?", (caminho.name,)).fetchone()
    inicio, linhas_antes, cabecalho = 0, 0, None
    if ponto:
        tamanho = caminho.stat().st_size
        if tamanho >= ponto["bytes_lidos"] and \
                _assinatura(caminho, ponto["bytes_lidos"]) == ponto["assinatura"]:
            inicio, linhas_antes, cabecalho = ponto["bytes_lidos"], ponto["linhas_lidas"], ponto["cabecalho"]
        else:
            resultado.reiniciado = True

    with open(caminho, "rb") as arquivo:
        arquivo.seek(inicio)
        bruto = arquivo.read()
    fim_completo = bruto.rfind(b"\n")
    if fim_completo < 0:
        return resultado  # nenhuma linha completa ainda
    consumido = bruto[: fim_completo + 1]
    try:
        texto = consumido.decode("utf-8-sig" if inicio == 0 else "utf-8")
    except UnicodeDecodeError:
        texto = consumido.decode("cp1252", errors="replace")  # CSV salvo pelo Excel em "ANSI"
    texto = texto.replace("\x00", "")  # restos de UTF-16 (ex.: `>>` do PowerShell 5)

    csv_arquivo = caminho.suffix.lower() == ".csv"
    deslocamento = 0  # linhas de cabeçalho consumidas neste trecho
    if csv_arquivo and inicio == 0:
        cabecalho, _, texto = texto.partition("\n")
        cabecalho = cabecalho.lstrip("\ufeff")
        cabecalho = cabecalho.rstrip("\r")
        deslocamento = 1
    if csv_arquivo:
        registros = _registros_csv(texto, cabecalho or "")
        decimal_virgula = (cabecalho or "").count(";") > (cabecalho or "").count(",")
    else:
        registros = _registros_jsonl(texto)
        decimal_virgula = False

    rejeitados = []
    with db.transacao(conexao):
        for i, (registro, linha) in enumerate(registros, start=linhas_antes + deslocamento + 1):
            if registro is None:
                continue  # linha em branco
            resultado.linhas_novas += 1
            if isinstance(registro, str):
                rejeitados.append((i, [registro], linha))
                continue
            if tipo == c.TIPO_LAUDO:
                normal, motivos = normalizar_laudo(registro)
            else:
                normal, motivos = normalizar_leitura(registro, decimal_virgula)
            if normal is None:
                rejeitados.append((i, motivos, linha))
                continue
            normal.update(arquivo=caminho.name, id_lote=id_lote)
            tabela = "laudos" if tipo == c.TIPO_LAUDO else "leituras"
            colunas = list(normal)
            chave = "chave" if tipo == c.TIPO_LAUDO else "id_leitura"
            cursor = conexao.execute(  # só o conflito de CHAVE é "já processado"; outro erro aparece
                f"INSERT INTO {tabela} ({', '.join(colunas)}) "
                f"VALUES ({', '.join('?' for _ in colunas)}) ON CONFLICT({chave}) DO NOTHING",
                [normal[k] for k in colunas])
            if cursor.rowcount == 1:
                resultado.novos += 1
                (resultado.laudos if tipo == c.TIPO_LAUDO else resultado.leituras).append(normal)
            else:
                resultado.duplicados += 1

        if resultado.reiniciado:  # relido do início: as rejeições antigas deste arquivo são substituídas
            conexao.execute("DELETE FROM rejeitados WHERE arquivo = ?", (caminho.name,))
        for linha_n, motivos, conteudo in rejeitados:
            conexao.execute(
                "INSERT INTO rejeitados (arquivo, linha, motivos, conteudo, id_lote) VALUES (?, ?, ?, ?, ?)",
                (caminho.name, linha_n, ",".join(motivos), (conteudo or "")[:2000], id_lote))
        resultado.rejeitados = len(rejeitados)

        total_bytes = inicio + len(consumido)
        total_linhas = linhas_antes + consumido.count(b"\n")
        momento = agora()
        conexao.execute(
            """INSERT INTO arquivos (nome, tipo, bytes_lidos, linhas_lidas, assinatura, cabecalho,
                                     primeira_leitura, ultima_leitura)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(nome) DO UPDATE SET bytes_lidos = excluded.bytes_lidos,
                   linhas_lidas = excluded.linhas_lidas, assinatura = excluded.assinatura,
                   cabecalho = excluded.cabecalho, ultima_leitura = excluded.ultima_leitura""",
            (caminho.name, tipo, total_bytes, total_linhas, _assinatura(caminho, total_bytes),
             cabecalho, momento, momento))
    return resultado
