"""Monitor da Fase 3: observa a pasta de entrada e reage aos dados novos.

    .venv\\Scripts\\python.exe ferramentas\\monitor_fase3.py                 # loop, a cada 5 s
    .venv\\Scripts\\python.exe ferramentas\\monitor_fase3.py --intervalo 2
    .venv\\Scripts\\python.exe ferramentas\\monitor_fase3.py --uma-vez       # um ciclo e sai
    .venv\\Scripts\\python.exe ferramentas\\monitor_fase3.py --estado        # checkpoint e totais
    .venv\\Scripts\\python.exe ferramentas\\monitor_fase3.py --reiniciar     # apaga estado e saídas

Depois de iniciado, não precisa de intervenção: cada arquivo novo (ou linha
acrescentada) em `dados/entrada/` é lido uma única vez, as regras são avaliadas
e as ações disparadas. O log vai para o console e para
`dados/fase3/logs/monitor.log`.
"""

from __future__ import annotations

import argparse
import json
import logging
import shutil
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from src.automacao.contratos import Ambiente, tipo_do_arquivo  # noqa: E402
from src.automacao.motor import Monitor, configurar_log, estado_json  # noqa: E402


def main() -> None:
    p = argparse.ArgumentParser(description="Monitor da pasta de entrada (micro-batch) da Fase 3.")
    p.add_argument("--intervalo", type=float, default=5.0, help="segundos entre varreduras")
    p.add_argument("--uma-vez", action="store_true", help="roda um ciclo e sai")
    p.add_argument("--estado", action="store_true", help="mostra checkpoint e totais e sai")
    p.add_argument("--reiniciar", action="store_true",
                   help="apaga dados/fase3 (estado, saídas, log) e a pasta de entrada antes de começar")
    p.add_argument("--entrada", type=Path, help="pasta monitorada (padrão: dados/entrada)")
    p.add_argument("--base", type=Path, help="pasta de estado e saídas (padrão: dados/fase3)")
    a = p.parse_args()

    padrao = Ambiente.padrao()
    ambiente = Ambiente(entrada=a.entrada or padrao.entrada, base=a.base or padrao.base)

    if a.estado:
        print(json.dumps(estado_json(ambiente), ensure_ascii=False, indent=2))
        return
    if a.reiniciar:
        if ambiente.base.exists():
            shutil.rmtree(ambiente.base)
        removidos = 0
        if ambiente.entrada.is_dir():  # só os arquivos que o monitor lê; a pasta e o resto ficam
            for arquivo in ambiente.entrada.iterdir():
                if arquivo.is_file() and (tipo_do_arquivo(arquivo.name) or arquivo.name.endswith(".tmp")):
                    arquivo.unlink()
                    removidos += 1
        print(f"  estado apagado: {ambiente.base} · {removidos} arquivo(s) de entrada removido(s)")

    configurar_log(ambiente)
    monitor = Monitor(ambiente)
    try:
        if a.uma_vez:
            if monitor.ciclo().ocioso:
                lidos = monitor.conexao.execute("SELECT COUNT(*) FROM arquivos").fetchone()[0]
                logging.getLogger("agrosmart.fase3").info(
                    "nenhum dado novo desde o último checkpoint (%d arquivo(s) já lidos): nada a processar", lidos)
        else:
            monitor.rodar(a.intervalo)
    finally:
        monitor.fechar()


if __name__ == "__main__":
    main()
