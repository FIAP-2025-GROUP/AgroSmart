"""Ações automáticas disparadas pelos alertas.

| Tipo             | O que faz                                                        |
|------------------|------------------------------------------------------------------|
| `arquivo_alerta` | grava `saida/alertas/alerta_<id>_<regra>.json` e atualiza o      |
|                  | consolidado `saida/alertas/alertas.csv`                          |
| `relatorio`      | gera o relatório de intervenção em Markdown (`saida/relatorios/`)|
| `irrigacao`      | envia o comando ao simulador de irrigação                        |
|                  | (`saida/irrigacao/comando_<id>.json`)                            |
| `email`          | monta o e-mail (`.eml`, com o relatório anexo); envia por SMTP se|
|                  | `AGROSMART_SMTP_HOST` estiver configurado, senão fica na caixa de|
|                  | saída `saida/emails/` — modo simulação                           |

Toda ação é **idempotente**: o arquivo de destino é nomeado pelo id do alerta,
então reexecutar uma ação pendente (depois de uma queda, por exemplo) reescreve
o mesmo arquivo em vez de criar outro. O e-mail por SMTP é a exceção natural —
ele só é tentado enquanto a ação estiver `pendente`.
"""

from __future__ import annotations

import csv
import json
import logging
import os
import re
import smtplib
import sqlite3
import time
import unicodedata
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from pathlib import Path

from .. import config  # noqa: F401 — carrega o .env (credenciais SMTP opcionais)
from . import armazenamento as db
from . import regras
from .contratos import VERSAO_REGRAS, Ambiente
from .ingestao import agora

log = logging.getLogger("agrosmart.fase3")

SEVERIDADE_TEXTO = {"critica": "CRÍTICA", "alta": "ALTA", "media": "MÉDIA", "informativa": "INFO"}


def _slug(texto: str | None) -> str:
    base = unicodedata.normalize("NFKD", texto or "sem-local").encode("ascii", "ignore").decode()
    return (re.sub(r"[^A-Za-z0-9]+", "-", base).strip("-") or "sem-local")[:40].strip("-")


def _base_nome(alerta: dict) -> str:
    return f"{alerta['id']:06d}_{alerta['regra']}_{_slug(alerta['propriedade'])}_{_slug(alerta['talhao'])}"


def _escrever(caminho: Path, conteudo: str | bytes) -> None:
    """Grava num temporário e renomeia: ninguém lê um arquivo pela metade."""
    caminho.parent.mkdir(parents=True, exist_ok=True)
    temporario = caminho.with_name(f".{caminho.name}.tmp")
    if isinstance(conteudo, bytes):
        temporario.write_bytes(conteudo)
    else:
        temporario.write_text(conteudo, encoding="utf-8")
    substituir(temporario, caminho)


def substituir(origem: Path, destino: Path, tentativas: int = 6) -> None:
    """`os.replace` tolerante ao Windows: espera um pouco se o destino estiver aberto por outro processo."""
    for tentativa in range(tentativas):
        try:
            os.replace(origem, destino)
            return
        except PermissionError:
            if tentativa == tentativas - 1:
                raise
            time.sleep(0.25 * (tentativa + 1))


def _rel(ambiente: Ambiente, destino: Path) -> str:
    return destino.relative_to(ambiente.base).as_posix()  # "/" também no Windows


# --------------------------------------------------------------------------- #
# Cada ação
# --------------------------------------------------------------------------- #
def arquivo_alerta(ambiente: Ambiente, alerta: dict) -> tuple[str, str]:
    destino = ambiente.alertas / f"alerta_{_base_nome(alerta)}.json"
    _escrever(destino, json.dumps(alerta, ensure_ascii=False, indent=2, default=str))
    return _rel(ambiente, destino), "arquivo de alerta gravado"


def _valor(chave: str, valor) -> str:
    if isinstance(valor, dict):
        return ", ".join(f"{k}: {v}" for k, v in valor.items())
    if isinstance(valor, list):
        return ", ".join(_valor(chave, v) if isinstance(v, dict) else str(v) for v in valor)
    if isinstance(valor, float) and (chave.startswith("proporcao") or chave == "limite"):
        return f"{valor:.1%}"
    if isinstance(valor, bool):
        return "sim" if valor else "não"
    return str(valor)


def relatorio(ambiente: Ambiente, alerta: dict) -> tuple[str, str]:
    regra = regras.POR_ID[alerta["regra"]]
    ev = alerta["evidencias"]
    plano = alerta["regra"] == "R2"
    titulo = "Plano de controle" if plano else "Relatório de intervenção"
    linhas_ev = "\n".join(f"| {k.replace('_', ' ')} | {_valor(k, v)} |" for k, v in ev.items())
    passos = [
        "Confirmar o diagnóstico em campo com o engenheiro agrônomo responsável (até 24 h).",
        f"Aplicar o manejo indicado: {alerta['recomendacao']}",
        "Isolar o talhão: higienizar ferramentas e calçados ao sair, evitar trânsito para talhões vizinhos.",
        "Fotografar 10 plantas por talhão em 3 dias e reenviar ao AgroSmart para medir a evolução.",
        "Registrar a aplicação (produto, dose, carência) no caderno de campo.",
    ] if plano else [
        "Vistoriar o talhão e confirmar o diagnóstico com o engenheiro agrônomo (até 24 h).",
        f"Se confirmado, aplicar o manejo indicado: {alerta['recomendacao']}",
        "Reavaliar o talhão com novas fotos em 3 dias.",
    ]
    texto = f"""# AgroSmart — {titulo}

**Alerta nº {alerta['id']}** · regra **{regra.id} — {regra.nome}** · severidade **{SEVERIDADE_TEXTO[alerta['severidade']]}**

| | |
|---|---|
| Local | {alerta['propriedade']} · talhão {alerta['talhao']} · {alerta['municipio'] or '—'}/{alerta['uf'] or '—'} |
| Cultura | {alerta['cultura'] or '—'} |
| Momento do dado | {alerta['instante_evento']} |
| Gerado em | {alerta['criado_em']} (micro-batch nº {alerta['id_lote']}) |

## Situação

{alerta['mensagem']}

## Regra que disparou

`SE {regra.expressao} ENTÃO {regra.acoes}`

## Evidências

| Indicador | Valor |
|---|---|
{linhas_ev}

## {'Plano de controle' if plano else 'Intervenção recomendada'}

{chr(10).join(f'{i}. [ ] {p}' for i, p in enumerate(passos, 1))}

---
*Gerado automaticamente pelo AgroSmart (regras v{VERSAO_REGRAS}). O diagnóstico por imagem é uma triagem:
a decisão de aplicar defensivo exige engenheiro agrônomo.*
"""
    destino = ambiente.relatorios / f"{'plano' if plano else 'intervencao'}_{_base_nome(alerta)}.md"
    _escrever(destino, texto)
    return _rel(ambiente, destino), titulo.lower() + " gerado"


def irrigacao(ambiente: Ambiente, alerta: dict) -> tuple[str, str]:
    comando = "LIGAR" if alerta["titulo"].startswith("Irrigação LIGADA") else "DESLIGAR"
    pacote = {
        "dispositivo": f"valvula-{_slug(alerta['propriedade']).lower()}-{_slug(alerta['talhao']).lower()}",
        "comando": comando, "id_alerta": alerta["id"], "motivo": alerta["mensagem"],
        "instante_evento": alerta["instante_evento"], "enviado_em": agora(), "simulado": True,
    }
    destino = ambiente.irrigacao / f"comando_{_base_nome(alerta)}.json"
    _escrever(destino, json.dumps(pacote, ensure_ascii=False, indent=2))
    return _rel(ambiente, destino), f"simulador: válvula {comando}"


def _configuracao_smtp() -> dict | None:
    host = (os.environ.get("AGROSMART_SMTP_HOST") or "").strip()
    if not host:
        return None
    return {
        "host": host,
        "porta": int(os.environ.get("AGROSMART_SMTP_PORTA") or 587),
        "usuario": os.environ.get("AGROSMART_SMTP_USUARIO") or None,
        "senha": os.environ.get("AGROSMART_SMTP_SENHA") or None,
        "tls": (os.environ.get("AGROSMART_SMTP_TLS") or "1").strip() not in ("0", "false", "nao", "não"),
    }


def email(ambiente: Ambiente, alerta: dict, anexo: Path | None) -> tuple[str, str]:
    remetente = os.environ.get("AGROSMART_EMAIL_DE") or "agrosmart@fazenda.example"
    destinatario = os.environ.get("AGROSMART_EMAIL_PARA") or "agronomo@fazenda.example"
    mensagem = EmailMessage()
    mensagem["From"] = f"AgroSmart <{remetente}>"
    mensagem["To"] = destinatario
    mensagem["Subject"] = f"[AgroSmart][{SEVERIDADE_TEXTO[alerta['severidade']]}] {alerta['titulo']}"
    mensagem["Date"] = formatdate(localtime=True)
    mensagem["Message-ID"] = make_msgid(idstring=f"alerta{alerta['id']}", domain="agrosmart.local")
    mensagem["X-AgroSmart-Alerta"] = str(alerta["id"])
    regra = regras.POR_ID[alerta["regra"]]
    mensagem.set_content(f"""{alerta['titulo']}

{alerta['mensagem']}

O que fazer:
{alerta['recomendacao']}

Regra {regra.id} ({regra.nome}): SE {regra.expressao}
Momento do dado: {alerta['instante_evento']}
Alerta nº {alerta['id']} · micro-batch nº {alerta['id_lote']}

--
AgroSmart · mensagem automática. O diagnóstico por imagem é uma triagem;
a aplicação de defensivo exige engenheiro agrônomo.
""")
    if anexo and anexo.is_file():
        mensagem.add_attachment(anexo.read_bytes(), maintype="text", subtype="markdown",
                                filename=anexo.name)

    destino = ambiente.emails / f"email_{_base_nome(alerta)}.eml"
    _escrever(destino, bytes(mensagem))
    smtp = _configuracao_smtp()
    if smtp is None:
        return _rel(ambiente, destino), f"simulado: e-mail para {destinatario} na caixa de saída"
    try:
        with smtplib.SMTP(smtp["host"], smtp["porta"], timeout=10) as servidor:
            if smtp["tls"]:
                servidor.starttls()
            if smtp["usuario"]:
                servidor.login(smtp["usuario"], smtp["senha"] or "")
            servidor.send_message(mensagem)
    except Exception as erro:
        erro.destino = _rel(ambiente, destino)  # o .eml existe: o painel ainda consegue mostrá-lo
        raise
    return _rel(ambiente, destino), f"enviado por SMTP ({smtp['host']}) para {destinatario}"


# --------------------------------------------------------------------------- #
# Executor (outbox)
# --------------------------------------------------------------------------- #
def _alerta(conexao: sqlite3.Connection, id_alerta: int) -> dict:
    alerta = dict(conexao.execute("SELECT * FROM alertas WHERE id = ?", (id_alerta,)).fetchone())
    alerta["evidencias"] = json.loads(alerta["evidencias"])
    return alerta


def exportar_consolidado(ambiente: Ambiente, conexao: sqlite3.Connection) -> None:
    """`alertas.csv` com todos os alertas (Excel PT-BR: `;` e utf-8-sig)."""
    registros = db.linhas(conexao.execute(
        "SELECT id, criado_em, instante_evento, regra, severidade, propriedade, talhao, cultura, "
        "titulo, mensagem, recomendacao, id_lote FROM alertas ORDER BY id"))
    destino = ambiente.alertas / "alertas.csv"
    destino.parent.mkdir(parents=True, exist_ok=True)
    temporario = destino.with_name(".alertas.csv.tmp")
    # (aberto no Excel no Windows o replace falha; o chamador registra e tenta de novo depois)
    with open(temporario, "w", encoding="utf-8-sig", newline="") as arquivo:
        escritor = csv.DictWriter(arquivo, fieldnames=list(registros[0]) if registros else ["id"],
                                  delimiter=";")
        escritor.writeheader()
        escritor.writerows(registros)
    substituir(temporario, destino)


MAX_TENTATIVAS = 3


def executar_pendentes(ambiente: Ambiente, conexao: sqlite3.Connection) -> list[dict]:
    """Executa as ações `pendente` na ordem do alerta e da `ORDEM_ACOES`.

    Cada ação é **reservada** antes (`pendente` → `executando`, só se ninguém reservou): dois monitores
    abertos por engano não mandam o mesmo e-mail duas vezes. Falha transitória (SMTP fora do ar) volta a
    `pendente` e é tentada de novo nos próximos ciclos, até `MAX_TENTATIVAS`; depois fica `falhou`.
    """
    pendentes = db.linhas(conexao.execute("SELECT * FROM acoes WHERE status = 'pendente'"))
    pendentes.sort(key=lambda a: (a["id_alerta"], regras.ORDEM_ACOES.index(a["tipo"])))
    executadas = []
    for acao in pendentes:
        reservada = conexao.execute("UPDATE acoes SET status = 'executando' WHERE id = ? AND status = 'pendente'",
                                    (acao["id"],)).rowcount
        if reservada != 1:
            continue  # outro processo pegou esta ação
        alerta = _alerta(conexao, acao["id_alerta"])
        try:
            if acao["tipo"] == regras.ACAO_ARQUIVO:
                destino, detalhe = arquivo_alerta(ambiente, alerta)
            elif acao["tipo"] == regras.ACAO_RELATORIO:
                destino, detalhe = relatorio(ambiente, alerta)
            elif acao["tipo"] == regras.ACAO_IRRIGACAO:
                destino, detalhe = irrigacao(ambiente, alerta)
            elif acao["tipo"] == regras.ACAO_EMAIL:
                feito = conexao.execute(
                    "SELECT destino FROM acoes WHERE id_alerta = ? AND tipo = ? AND status = 'executada'",
                    (alerta["id"], regras.ACAO_RELATORIO)).fetchone()
                destino, detalhe = email(ambiente, alerta, ambiente.base / feito["destino"] if feito else None)
            else:
                raise ValueError(f"tipo de ação desconhecido: {acao['tipo']}")
            status = "executada"
        except Exception as erro:  # uma ação que falha não derruba o monitor
            tentativas = (acao.get("tentativas") or 0) + 1
            destino = getattr(erro, "destino", None)
            status = "pendente" if tentativas < MAX_TENTATIVAS else "falhou"
            detalhe = f"tentativa {tentativas}/{MAX_TENTATIVAS}: {type(erro).__name__}: {erro}"
            log.error("ação %s do alerta %s falhou (%s)", acao["tipo"], alerta["id"], detalhe)
            conexao.execute("UPDATE acoes SET tentativas = ? WHERE id = ?", (tentativas, acao["id"]))
        conexao.execute("UPDATE acoes SET status = ?, destino = ?, detalhe = ?, executada_em = ? WHERE id = ?",
                        (status, destino, detalhe, agora(), acao["id"]))
        executadas.append({**acao, "status": status, "destino": destino, "detalhe": detalhe,
                           "regra": alerta["regra"], "titulo": alerta["titulo"]})
    if executadas:
        try:
            exportar_consolidado(ambiente, conexao)
        except OSError as erro:  # ex.: alertas.csv aberto no Excel — os .json individuais já estão gravados
            log.warning("alertas.csv não atualizado agora (%s); será regravado no próximo ciclo com ação", erro)
    return executadas
