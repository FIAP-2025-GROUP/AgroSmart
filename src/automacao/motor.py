"""Orquestração do micro-batch: ingestão → regras → alertas → ações.

Um **ciclo** do monitor:

1. procura arquivos com bytes ainda não consumidos na pasta monitorada;
2. se houver, abre um **lote** e consome só as linhas novas de cada arquivo
   (registros + checkpoint numa transação por arquivo);
3. avalia as regras **somente** para os talhões e dias tocados pelos registros
   novos do lote — dado repetido não entra no lote, então não dispara nada;
4. grava os alertas (chave de deduplicação única) e as ações pendentes, e marca
   o lote como avaliado, tudo numa transação;
5. executa as ações pendentes (e-mail, relatório, irrigação, arquivo).

Se o processo cair entre os passos 2 e 4, o lote fica sem `concluido_em` e é
reavaliado no próximo ciclo; a chave única do alerta impede duplicatas. Se cair
entre 4 e 5, as ações continuam `pendente` e são executadas no ciclo seguinte.

As regras usam o **tempo do evento** (`instante` do dado), não o do
processamento: um lote com dados de três dias atrás é avaliado como se fosse
naquele dia. Laudos que chegam fora de ordem completam as janelas de R1/R2.
Limites conhecidos: a R5 é avaliada na chegada da **leitura** (um laudo de
praga que chega depois não reavalia leituras antigas), e uma leitura anterior
ao último comando de irrigação não reverte a válvula (R3).
"""

from __future__ import annotations

import json
import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

from ..pipeline import contratos as c2
from . import acoes as executor
from . import armazenamento as db
from . import ingestao, regras
from .contratos import LIMITES as L
from .contratos import Ambiente

log = logging.getLogger("agrosmart.fase3")


@dataclass
class ResumoCiclo:
    id_lote: int | None
    arquivos: list[dict] = field(default_factory=list)
    novos: int = 0
    duplicados: int = 0
    rejeitados: int = 0
    alertas: list[dict] = field(default_factory=list)
    acoes: list[dict] = field(default_factory=list)

    @property
    def ocioso(self) -> bool:
        return self.id_lote is None and not self.acoes


class _Alertas:
    """Alertas de um talhão já gravados + os decididos neste lote (para o NOT)."""

    def __init__(self, conexao, propriedade: str, talhao: str):
        self.itens = [(r["regra"], r["chave_dedup"], datetime.fromisoformat(r["instante_evento"]))
                      for r in conexao.execute(
                          "SELECT regra, chave_dedup, instante_evento FROM alertas "
                          "WHERE propriedade = ? AND talhao = ?", (propriedade, talhao))]

    def adicionar(self, disparo: regras.Disparo) -> None:
        self.itens.append((disparo.regra, disparo.chave_dedup, datetime.fromisoformat(disparo.instante_evento)))

    def recente(self, regra: str, quando: datetime, janela: timedelta, filtro: str | None = None) -> bool:
        """Há alerta da regra a menos de `janela` do instante (antes ou depois, por dados fora de ordem)?"""
        return any(r == regra and abs(quando - t) < janela and (filtro is None or filtro in chave)
                   for r, chave, t in self.itens)


def _dia(texto: str) -> date:
    return date.fromisoformat(texto[:10])


def _fim_do_dia(d: date) -> datetime:
    return datetime.fromisoformat(f"{d.isoformat()}T23:59:59-03:00")


# --------------------------------------------------------------------------- #
# Avaliação das regras para um lote
# --------------------------------------------------------------------------- #
def _avaliar_laudos(conexao, id_lote: int, estado_alertas) -> list[regras.Disparo]:
    novos = db.linhas(conexao.execute(
        "SELECT propriedade, talhao, dia FROM laudos WHERE id_lote = ?", (id_lote,)))
    afetados: dict[tuple, set[date]] = defaultdict(set)
    for linha in novos:
        if linha["propriedade"] and linha["talhao"]:
            afetados[(linha["propriedade"], linha["talhao"])].add(_dia(linha["dia"]))

    disparos = []
    for (propriedade, talhao), dias in sorted(afetados.items()):
        todos = db.linhas(conexao.execute(
            "SELECT * FROM laudos WHERE propriedade = ? AND talhao = ?", (propriedade, talhao)))
        por_analise = regras.analises(todos)
        dias_com_dado = {_dia(a["dia"]) for a in por_analise}
        alertas = estado_alertas(propriedade, talhao)

        # Um dado novo do dia D pode completar janelas que terminam em D..D+horizonte.
        horizonte = max(L["r1_janela_dias"], L["r2_dias_seguidos"]) - 1
        avaliar = sorted({d + timedelta(days=k) for d in dias for k in range(horizonte + 1)
                          if k == 0 or d + timedelta(days=k) in dias_com_dado})

        problemas: dict[date, dict[str, dict]] = defaultdict(dict)
        for a in sorted(por_analise, key=lambda x: x["instante"]):
            if a["condicao"] == c2.CONDICAO_DOENTE and a["categoria"] in (c2.CATEGORIA_DOENCA, c2.CATEGORIA_PRAGA):
                problemas[_dia(a["dia"])].setdefault(a["diagnostico"], a)

        for d in avaliar:
            inicio = d - timedelta(days=L["r1_janela_dias"] - 1)
            janela = [a for a in por_analise if inicio <= _dia(a["dia"]) <= d]
            ja = alertas.recente("R1", _fim_do_dia(d), timedelta(days=L["r1_carencia_dias"]))
            disparo = regras.r1_infestacao(d, janela, ja)
            if disparo:
                alertas.adicionar(disparo)
                disparos.append(disparo)

            def ja_planejado(diagnostico, d=d):
                return alertas.recente("R2", _fim_do_dia(d), timedelta(days=L["r2_carencia_dias"]),
                                       filtro=f"|{diagnostico}|")
            for disparo in regras.r2_persistencia(d, problemas, ja_planejado):
                alertas.adicionar(disparo)
                disparos.append(disparo)
    return disparos


def _avaliar_leituras(conexao, id_lote: int, estado_alertas) -> list[regras.Disparo]:
    novas = db.linhas(conexao.execute(
        "SELECT * FROM leituras WHERE id_lote = ? ORDER BY instante, id_leitura", (id_lote,)))
    por_talhao: dict[tuple, list[dict]] = defaultdict(list)
    for leitura in novas:
        por_talhao[(leitura["propriedade"], leitura["talhao"])].append(leitura)

    disparos = []
    for (propriedade, talhao), lista in sorted(por_talhao.items()):
        historico = db.linhas(conexao.execute(
            "SELECT * FROM leituras WHERE propriedade = ? AND talhao = ? ORDER BY instante, id_leitura",
            (propriedade, talhao)))
        instantes = [datetime.fromisoformat(x["instante"]) for x in historico]
        laudos_praga = {_dia(a["dia"]) for a in regras.analises(db.linhas(conexao.execute(
            "SELECT * FROM laudos WHERE propriedade = ? AND talhao = ?", (propriedade, talhao))))
            if a["categoria"] == c2.CATEGORIA_PRAGA}
        estado = conexao.execute("SELECT ligada, desde FROM irrigacao WHERE propriedade = ? AND talhao = ?",
                                 (propriedade, talhao)).fetchone()
        ligada = bool(estado["ligada"]) if estado else False
        ultimo_comando = datetime.fromisoformat(estado["desde"]) if estado else None
        alertas = estado_alertas(propriedade, talhao)

        for leitura in lista:
            quando = datetime.fromisoformat(leitura["instante"])
            janela_chuva = quando - timedelta(hours=L["r3_janela_chuva_horas"])
            chuva = sum(x["chuva_mm"] for x, t in zip(historico, instantes) if janela_chuva < t <= quando)
            # Leitura atrasada, anterior ao último comando enviado à válvula, não reverte o estado atual.
            atrasada = ultimo_comando is not None and quando < ultimo_comando
            disparo = None if atrasada else regras.r3_irrigacao(leitura, chuva, ligada)
            if disparo:
                ligada = disparo.comando_irrigacao == "ligar"
                ultimo_comando = quando
                alertas.adicionar(disparo)
                disparos.append(disparo)

            ate_agora = [x for x, t in zip(historico, instantes) if t <= quando]
            ja = alertas.recente("R4", quando, timedelta(hours=L["r4_carencia_horas"]))
            disparo = regras.r4_requeima(ate_agora, ja)
            if disparo:
                alertas.adicionar(disparo)
                disparos.append(disparo)

            hoje = quando.date()
            recente = any(hoje - timedelta(days=L["r5_janela_laudo_dias"] - 1) <= d <= hoje for d in laudos_praga)
            ja = alertas.recente("R5", quando, timedelta(hours=L["r5_carencia_horas"]))
            disparo = regras.r5_pragas(leitura, recente, ja)
            if disparo:
                alertas.adicionar(disparo)
                disparos.append(disparo)
    return disparos


def _gravar(conexao, id_lote: int, disparos: list[regras.Disparo]) -> list[dict]:
    """Alertas + ações pendentes + estado da irrigação (chamar dentro de transação)."""
    gravados = []
    momento = ingestao.agora()
    for d in disparos:
        local = d.local
        cursor = conexao.execute(
            """INSERT OR IGNORE INTO alertas (chave_dedup, regra, severidade, propriedade, talhao, cultura,
                   uf, municipio, titulo, mensagem, recomendacao, evidencias, instante_evento, criado_em, id_lote)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (d.chave_dedup, d.regra, d.severidade, local.get("propriedade"), local.get("talhao"),
             local.get("cultura"), local.get("uf"), local.get("municipio"), d.titulo, d.mensagem,
             d.recomendacao, db.json_texto(d.evidencias), d.instante_evento, momento, id_lote))
        if cursor.rowcount != 1:
            continue  # o mesmo alerta já existia: nada novo a disparar
        id_alerta = cursor.lastrowid
        for tipo in d.acoes:
            conexao.execute("INSERT INTO acoes (id_alerta, tipo, status, criada_em) VALUES (?, ?, 'pendente', ?)",
                            (id_alerta, tipo, momento))
        if d.comando_irrigacao:
            conexao.execute(
                """INSERT INTO irrigacao (propriedade, talhao, ligada, desde, motivo) VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(propriedade, talhao) DO UPDATE SET ligada = excluded.ligada,
                       desde = excluded.desde, motivo = excluded.motivo""",
                (local["propriedade"], local["talhao"], int(d.comando_irrigacao == "ligar"),
                 d.instante_evento, d.mensagem))
        gravados.append({"id": id_alerta, "regra": d.regra, "severidade": d.severidade, "titulo": d.titulo,
                         "mensagem": d.mensagem, "acoes": list(d.acoes), "instante_evento": d.instante_evento})
    return gravados


# --------------------------------------------------------------------------- #
# Monitor
# --------------------------------------------------------------------------- #
class Monitor:
    def __init__(self, ambiente: Ambiente | None = None):
        self.ambiente = ambiente or Ambiente.padrao()
        self.ambiente.criar_pastas()
        self.conexao = db.conectar(self.ambiente.banco)
        self.intervalo: float | None = None
        # Ação reservada por um processo que caiu antes de terminar volta para a fila.
        self.conexao.execute("UPDATE acoes SET status = 'pendente' WHERE status = 'executando'")

    def fechar(self) -> None:
        try:  # incorpora o WAL ao estado.db: uma cópia do arquivo fica completa
            self.conexao.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except Exception:
            pass
        self.conexao.close()

    def ciclo(self) -> ResumoCiclo:
        conexao = self.conexao
        arquivos = ingestao.pendentes(conexao, self.ambiente)
        resumo = ResumoCiclo(id_lote=None)

        if arquivos:
            with db.transacao(conexao):
                resumo.id_lote = conexao.execute(
                    "INSERT INTO lotes (iniciado_em) VALUES (?)", (ingestao.agora(),)).lastrowid
            log.info("[LOTE %d] %d arquivo(s) com dados novos na pasta monitorada", resumo.id_lote, len(arquivos))
            for caminho, tipo in arquivos:
                try:
                    r = ingestao.processar_arquivo(conexao, caminho, tipo, resumo.id_lote)
                except OSError as erro:  # travado/removido entre a varredura e a leitura
                    log.warning("  %s não pôde ser lido agora (%s); tentará no próximo ciclo", caminho.name, erro)
                    continue
                if r.linhas_novas == 0:
                    continue  # ainda sem linha completa
                resumo.novos += r.novos
                resumo.duplicados += r.duplicados
                resumo.rejeitados += r.rejeitados
                resumo.arquivos.append({"nome": r.nome, "tipo": r.tipo, "linhas": r.linhas_novas, "novos": r.novos,
                                        "duplicados": r.duplicados, "rejeitados": r.rejeitados,
                                        "reiniciado": r.reiniciado})
                log.info("  %-34s %3d linha(s) lida(s): %3d nova(s) · %3d já processada(s) · %d rejeitada(s)%s",
                         r.nome, r.linhas_novas, r.novos, r.duplicados, r.rejeitados,
                         " (arquivo reescrito: relido do início)" if r.reiniciado else "")

        # Avalia o lote atual e qualquer lote que tenha ficado sem avaliação (queda no meio).
        abertos = [r["id"] for r in conexao.execute("SELECT id FROM lotes WHERE concluido_em IS NULL ORDER BY id")]
        for id_lote in abertos:
            cache: dict[tuple, _Alertas] = {}

            def estado_alertas(propriedade, talhao):
                if (propriedade, talhao) not in cache:
                    cache[(propriedade, talhao)] = _Alertas(conexao, propriedade, talhao)
                return cache[(propriedade, talhao)]

            disparos = _avaliar_laudos(conexao, id_lote, estado_alertas) + \
                _avaliar_leituras(conexao, id_lote, estado_alertas)
            with db.transacao(conexao):
                gravados = _gravar(conexao, id_lote, disparos)
                if id_lote == resumo.id_lote:
                    conexao.execute(
                        "UPDATE lotes SET concluido_em = ?, arquivos = ?, novos = ?, duplicados = ?, rejeitados = ?, "
                        "alertas = ? WHERE id = ?",
                        (ingestao.agora(), db.json_texto([a["nome"] for a in resumo.arquivos]), resumo.novos,
                         resumo.duplicados, resumo.rejeitados, len(gravados), id_lote))
                else:
                    conexao.execute("UPDATE lotes SET concluido_em = ?, alertas = alertas + ? WHERE id = ?",
                                    (ingestao.agora(), len(gravados), id_lote))
            resumo.alertas += gravados
            for a in gravados:
                log.info("  [ALERTA %d] %s [%s] %s", a["id"], a["regra"], a["severidade"].upper(), a["titulo"])

        if resumo.id_lote is not None and not resumo.alertas:
            if resumo.novos == 0 and resumo.duplicados:
                log.info("  nenhum registro novo (todos já processados): nenhuma ação disparada")
            else:
                log.info("  regras avaliadas: nenhuma condição atendida, nenhuma ação")

        resumo.acoes = executor.executar_pendentes(self.ambiente, conexao)
        for a in resumo.acoes:
            log.info("  [AÇÃO] alerta %d · %-14s %-9s %s%s", a["id_alerta"], a["tipo"], a["status"], a["detalhe"],
                     f" → {a['destino']}" if a["destino"] else "")
        if resumo.id_lote is not None:
            conexao.execute("UPDATE lotes SET acoes = ? WHERE id = ?",
                            (sum(1 for a in resumo.acoes if a["status"] == "executada"), resumo.id_lote))
        if resumo.id_lote is not None:
            conexao.execute("PRAGMA wal_checkpoint(PASSIVE)")
        self._pulso()
        return resumo

    def _pulso(self) -> None:
        """`pulso.json`: o painel usa para mostrar se o monitor está rodando."""
        destino = self.ambiente.base / "pulso.json"
        temporario = destino.with_name(".pulso.json.tmp")
        try:
            temporario.write_text(json.dumps({"ultima_varredura": ingestao.agora(), "intervalo_s": self.intervalo}),
                                  encoding="utf-8")
            executor.substituir(temporario, destino)
        except OSError:
            pass  # o pulso é só informativo para o painel; nunca derruba o monitor

    def rodar(self, intervalo: float = 5.0, ciclos: int | None = None) -> None:
        """Loop do monitor. `ciclos=None` roda até Ctrl+C."""
        self.intervalo = intervalo
        log.info("Monitor da Fase 3 observando %s (a cada %.0f s). Ctrl+C para parar.",
                 self.ambiente.entrada, intervalo)
        feitos, avisou_ocioso = 0, False
        try:
            while ciclos is None or feitos < ciclos:
                try:
                    resumo = self.ciclo()
                except Exception:  # erro transitório (arquivo travado, disco...): registra e segue no próximo ciclo
                    log.exception("erro no ciclo; o monitor continua e tenta de novo em %.0f s", intervalo)
                    resumo = ResumoCiclo(id_lote=None)
                    avisou_ocioso = True
                feitos += 1
                if resumo.ocioso:
                    if not avisou_ocioso:
                        log.info("aguardando dados novos...")
                        avisou_ocioso = True
                else:
                    avisou_ocioso = False
                if ciclos is None or feitos < ciclos:
                    time.sleep(intervalo)
        except KeyboardInterrupt:
            log.info("Monitor encerrado pelo usuário.")


def configurar_log(ambiente: Ambiente, arquivo: Path | None = None) -> None:
    """Console + arquivo `dados/fase3/logs/monitor.log` (UTF-8)."""
    ambiente.criar_pastas()
    raiz = logging.getLogger("agrosmart.fase3")
    raiz.setLevel(logging.INFO)
    raiz.handlers.clear()
    formato = logging.Formatter("%(asctime)s  %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    for handler in (logging.StreamHandler(), logging.FileHandler(arquivo or ambiente.logs / "monitor.log",
                                                                  encoding="utf-8")):
        handler.setFormatter(formato)
        raiz.addHandler(handler)
    raiz.propagate = False


def estado_json(ambiente: Ambiente) -> dict:
    """Resumo para depuração rápida (`monitor_fase3.py --estado`)."""
    conexao = db.conectar_leitura(ambiente.banco)
    if conexao is None:
        return {"banco": None}
    with conexao:
        return {
            "arquivos": db.linhas(conexao.execute("SELECT nome, tipo, bytes_lidos, linhas_lidas FROM arquivos")),
            "totais": {t: conexao.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                       for t in ("laudos", "leituras", "rejeitados", "lotes", "alertas", "acoes")},
            "irrigacao": db.linhas(conexao.execute("SELECT * FROM irrigacao")),
        }

