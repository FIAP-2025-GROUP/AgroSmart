"""Produtor de dados da Fase 3: publica micro-batches na pasta monitorada.

Simula a chegada de dados do campo em `dados/entrada/`:

* `laudos_<dia>.jsonl` — laudos de diagnóstico por imagem, no **mesmo contrato
  da Fase 2** (o que o app da Fase 1 emite), com as classes, sintomas e manejo
  do catálogo do CNN;
* `sensores_<dia>.csv` — leituras das estações de campo (umidade do solo,
  temperatura, umidade do ar, chuva e armadilha de pragas), 3 por dia por talhão.

Uso:
    .venv\\Scripts\\python.exe ferramentas\\gerar_dados_fase3.py --dia 1          # publica o dia 1
    .venv\\Scripts\\python.exe ferramentas\\gerar_dados_fase3.py --todos          # os 7 dias de uma vez
    .venv\\Scripts\\python.exe ferramentas\\gerar_dados_fase3.py --continuo --intervalo 8
        # dia a dia: laudos, depois as leituras de 06h, 12h e 18h ACRESCENTADAS ao mesmo CSV
    .venv\\Scripts\\python.exe ferramentas\\gerar_dados_fase3.py --reenviar 4     # mesmo conteúdo, outro nome
    .venv\\Scripts\\python.exe ferramentas\\gerar_dados_fase3.py --fase2 2026-07-06 --dias 3
        # reaproveita os laudos simulados da Fase 2 (dados/simulados/laudos_simulados.jsonl)

Cada arquivo novo é escrito com nome temporário (`.nome.tmp`) e renomeado no
fim — o monitor nunca vê um arquivo pela metade. Acréscimos a um CSV existente
são feitos uma linha completa por vez; o monitor só consome linhas terminadas
em quebra de linha.

O cenário (7 dias, 26/10 a 01/11/2026) é **simulado e determinístico**: mesma
semente, mesmos arquivos. Ele foi desenhado para exercitar cada regra:

| Talhão                         | O que acontece                                                |
|--------------------------------|---------------------------------------------------------------|
| Sítio Boa Esperança · T01 Batata | solo secando → irrigação liga (D3), desliga por umidade (D4), |
|                                | liga em nível crítico (D6), desliga por chuva (D7)             |
| Sítio Boa Esperança · T02 Tomate | clima de requeima (D2) → requeima aparece (D3) → infestação  |
|                                | > 30% (D4, R1) → 3 dias seguidos (D5, R2: plano de controle)   |
| Sítio Boa Esperança · T03 Tomate | ácaro-rajado em laudo (D4) + armadilha subindo → R5 (D5, D6) e |
|                                | plano de controle por persistência da praga (D6)               |
| Fazenda Santa Clara · Gleba 2 Milho | controle: tudo normal, nenhuma regra deve disparar; uma     |
|                                | leitura com sensor defeituoso é rejeitada (D3)                 |

Não representa a situação de nenhuma lavoura real.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import random
import shutil
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from src import rotulos  # noqa: E402
from src.automacao import contratos as c  # noqa: E402
from src.pipeline import contratos as c2  # noqa: E402
from src.tipos import MOTOR_CNN, MOTOR_VLM  # noqa: E402

SEMENTE = 20261026
INICIO = date(2026, 10, 26)
DIAS = 7
FUSO = timezone(timedelta(hours=-3))
INSTANCIA = "gerador-fase3"
HORAS = (6, 12, 18)

LOCAIS = {
    "SBE": {"uf": "SP", "municipio": "Campinas", "propriedade": "Sítio Boa Esperança"},
    "FSC": {"uf": "PR", "municipio": "Cascavel", "propriedade": "Fazenda Santa Clara"},
}
SBE, FSC = "SBE", "FSC"

# (umidade_solo, temperatura, umidade_ar, chuva, pragas) às 06h, 12h e 18h, por dia.
SENSORES = {
    ("T01", "Batata", "est-sbe-T01", SBE): [
        [(34.0, 18.2, 74, 0, 2), (31.2, 26.1, 55, 0, 2), (29.4, 23.0, 63, 0, 3)],
        [(27.6, 18.9, 70, 0, 2), (26.1, 27.4, 50, 0, 3), (25.2, 24.1, 58, 0, 2)],
        [(24.1, 19.3, 68, 0, 3), (29.0, 27.9, 48, 0, 3), (33.1, 24.6, 57, 0, 2)],   # liga às 06h
        [(36.2, 18.7, 71, 0, 2), (34.4, 27.0, 52, 0, 2), (32.3, 24.0, 60, 0, 3)],   # desliga às 06h
        [(29.8, 20.1, 60, 0, 3), (26.9, 31.5, 38, 0, 2), (25.0, 28.2, 44, 0, 3)],
        [(14.6, 22.4, 41, 0, 3), (20.3, 33.8, 33, 0, 4), (27.5, 29.0, 40, 0, 3)],   # liga crítico
        [(31.0, 17.6, 96, 12.4, 1), (33.6, 21.2, 88, 3.0, 1), (35.2, 20.4, 85, 0, 1)],  # desliga por chuva
    ],
    ("T02", "Tomate", "est-sbe-T02", SBE): [
        [(32.0, 19.0, 78, 0, 2), (31.1, 25.8, 60, 0, 1), (30.6, 22.3, 70, 0, 2)],
        [(31.8, 17.1, 93, 0.4, 1), (31.0, 20.6, 91, 0.2, 2), (30.7, 18.4, 94, 0.6, 1)],  # clima de requeima
        [(30.9, 16.2, 95, 1.2, 2), (30.1, 23.9, 80, 0, 2), (29.8, 21.0, 84, 0, 1)],
        [(31.5, 18.0, 82, 0, 2), (30.4, 25.2, 66, 0, 3), (30.0, 22.1, 72, 0, 2)],
        [(30.8, 18.6, 79, 0, 1), (30.0, 26.3, 62, 0, 2), (29.5, 22.8, 71, 0, 2)],
        [(30.2, 15.4, 92, 2.0, 2), (29.9, 19.2, 91, 1.1, 1), (29.6, 17.9, 90, 0.3, 2)],  # de novo
        [(32.4, 16.8, 94, 6.0, 1), (32.0, 20.0, 86, 1.5, 1), (31.7, 19.1, 83, 0, 1)],
    ],
    ("T03", "Tomate", "est-sbe-T03", SBE): [
        [(30.4, 21.0, 58, 0, 3), (29.6, 30.2, 44, 0, 4), (29.1, 27.0, 50, 0, 5)],
        [(30.0, 21.6, 55, 0, 4), (29.2, 30.8, 42, 0, 5), (28.8, 27.5, 48, 0, 6)],
        [(29.7, 22.0, 54, 0, 5), (28.9, 31.4, 40, 0, 6), (28.4, 28.1, 47, 0, 7)],
        [(29.5, 22.3, 52, 0, 6), (28.7, 31.9, 39, 0, 8), (28.2, 28.4, 45, 0, 9)],
        [(29.3, 22.5, 50, 0, 8), (28.4, 32.2, 38, 0, 12), (28.0, 28.8, 44, 0, 14)],  # atenção + laudo
        [(29.0, 22.8, 49, 0, 15), (28.1, 32.6, 37, 0, 21), (27.7, 29.0, 43, 0, 24)],  # nível de controle
        [(31.9, 18.0, 90, 10.2, 18), (31.4, 22.4, 80, 2.0, 16), (31.0, 21.0, 78, 0, 15)],
    ],
    ("Gleba 2", "Milho", "est-fsc-G2", FSC): [
        [(33.0, 16.0, 75, 0, 2), (32.1, 25.0, 58, 0, 3), (31.6, 21.4, 66, 0, 2)],
        [(32.5, 16.4, 74, 0, 3), (31.8, 25.6, 56, 0, 2), (31.2, 21.9, 64, 0, 3)],
        [(32.0, 16.9, 72, 0, 2), (31.3, 26.0, 55, 0, 3), (140.0, 22.0, 63, 0, 2)],  # sensor defeituoso
        [(31.6, 17.2, 71, 0, 3), (30.9, 26.4, 54, 0, 2), (30.5, 22.6, 62, 0, 3)],
        [(31.2, 17.5, 70, 0, 2), (30.5, 26.8, 53, 0, 3), (30.1, 23.0, 61, 0, 2)],
        [(34.8, 15.8, 88, 8.0, 2), (34.0, 21.5, 77, 1.0, 2), (33.6, 19.8, 74, 0, 3)],
        [(33.9, 16.2, 80, 0, 2), (33.1, 24.8, 61, 0, 3), (32.6, 21.2, 68, 0, 2)],
    ],
}

# Análises por dia: (saudáveis, doentes, indeterminadas) e o diagnóstico doente.
LAUDOS = {
    ("T01", "Batata", SBE): {"doenca": "Pinta-preta",
                             "dias": [(3, 0, 0), (3, 0, 0), (3, 0, 0), (3, 0, 0), (2, 1, 0), (3, 0, 0), (3, 0, 0)]},
    ("T02", "Tomate", SBE): {"doenca": "Requeima",
                             "dias": [(5, 0, 0), (5, 0, 1), (4, 2, 0), (1, 5, 0), (2, 3, 0), (3, 2, 0), (4, 1, 0)],
                             "comparacao": {3}},  # D4: duas análises com CNN + VLM
    ("T03", "Tomate", SBE): {"doenca": "Ácaro-rajado",
                             "dias": [(4, 0, 0), (4, 0, 0), (4, 0, 0), (3, 1, 0), (3, 1, 0), (2, 2, 0), (3, 1, 0)]},
    ("Gleba 2", "Milho", FSC): {"doenca": "Ferrugem comum",
                                "dias": [(4, 0, 0), (4, 0, 0), (4, 0, 0), (4, 0, 0), (4, 0, 0), (3, 1, 0), (4, 0, 0)]},
}

VLM_TEXTO = {"Requeima": ("Míldio tardio (Phytophthora infestans)", "Oomiceto (Phytophthora infestans)"),
             "Ácaro-rajado": ("Ácaro-rajado do tomateiro", "Praga (Tetranychus urticae)")}


def _dia(n: int) -> date:
    return INICIO + timedelta(days=n - 1)


def _classe(cultura: str, diagnostico: str) -> dict:
    return next(k for k in rotulos.CLASSES if k["cultura"] == cultura and k["diagnostico"] == diagnostico)


def _laudo(rng, n_dia, talhao, cultura, local, i, condicao, doenca, motor=MOTOR_CNN, id_analise=None,
           instante=None, modo="Automático (CNN → VLM)"):
    d = _dia(n_dia)
    instante = instante or datetime(d.year, d.month, d.day, rng.randint(7, 16), rng.randint(0, 59),
                                    rng.randint(0, 59), tzinfo=FUSO)
    sufixo = f"{d:%Y%m%d}-{talhao.replace(' ', '')}-{i:03d}"
    registro = c2.registro_vazio()
    registro.update(
        versao_schema=c2.VERSAO_SCHEMA, origem_instancia=INSTANCIA,
        id_analise=id_analise or f"f3-an-{sufixo}",
        id_laudo=f"f3-ld-{sufixo}{'-vlm' if motor == MOTOR_VLM else ''}",
        id_imagem="f3-img-" + hashlib.sha256(sufixo.encode()).hexdigest()[:12],
        arquivo=f"IMG_{d:%Y%m%d}_{talhao.replace(' ', '')}_{i:03d}.jpg",
        data_analise=instante.isoformat(timespec="seconds"), fuso_origem="America/Sao_Paulo",
        **local, talhao=talhao, modo_solicitado=modo, limiar_cnn=rotulos.LIMIAR_CONFIANCA,
        origem_dado=c2.ORIGEM_SIMULADO, tipo_amostra="foto_campo", motor=motor,
    )
    if condicao == "indeterminado":
        conf = round(rng.uniform(0.32, 0.55), 3)
        registro.update(especie_original="—", condicao=rotulos.CONDICAO_INDETERMINADA,
                        diagnostico_original="Indeterminado", agente_original="—",
                        modelo_versao="MobileNetV2 v1.0.0", confianca=conf, confianca_texto=f"{conf:.1%}",
                        observacoes="Nenhuma classe atingiu o limiar de confiança (foto desfocada).")
        return registro
    classe = _classe(cultura, "Saudável" if condicao == "saudavel" else doenca)
    if motor == MOTOR_VLM:
        diag, agente = VLM_TEXTO.get(classe["diagnostico"], (classe["diagnostico"], classe["agente"]))
        registro.update(especie_original=f"{'Tomateiro (Solanum lycopersicum)' if cultura == 'Tomate' else cultura}",
                        condicao=classe["condicao"], diagnostico_original=diag, agente_original=agente,
                        modelo_versao="gemini-3.6-flash", confianca=None, confianca_texto="alta (autoavaliada)",
                        sintomas=classe["sintomas"], manejo=classe["manejo"])
        return registro
    conf = round(rng.uniform(0.74, 0.97), 3)
    registro.update(especie_original=cultura, condicao=classe["condicao"], diagnostico_original=classe["diagnostico"],
                    agente_original=classe["agente"], modelo_versao="MobileNetV2 v1.0.0", confianca=conf,
                    confianca_texto=f"{conf:.1%}", sintomas=classe["sintomas"], manejo=classe["manejo"])
    return registro


def laudos_do_dia(n_dia: int) -> list[str]:
    rng = random.Random(SEMENTE * 10 + n_dia)
    linhas = []
    for (talhao, cultura, chave_local), plano in LAUDOS.items():
        local = LOCAIS[chave_local]
        saudaveis, doentes, indeterminadas = plano["dias"][n_dia - 1]
        condicoes = ["doente"] * doentes + ["saudavel"] * saudaveis + ["indeterminado"] * indeterminadas
        for i, condicao in enumerate(condicoes, 1):
            if n_dia - 1 in plano.get("comparacao", set()) and i <= 2:
                cnn = _laudo(rng, n_dia, talhao, cultura, local, i, condicao, plano["doenca"],
                             modo="Comparar os dois motores")
                vlm = _laudo(rng, n_dia, talhao, cultura, local, i, condicao, plano["doenca"], motor=MOTOR_VLM,
                             id_analise=cnn["id_analise"], instante=datetime.fromisoformat(cnn["data_analise"]),
                             modo="Comparar os dois motores")
                linhas += [json.dumps(cnn, ensure_ascii=False), json.dumps(vlm, ensure_ascii=False)]
            else:
                linhas.append(json.dumps(_laudo(rng, n_dia, talhao, cultura, local, i, condicao, plano["doenca"]),
                                         ensure_ascii=False))
    rng.shuffle(linhas)
    if n_dia == 5:
        linhas.insert(3, '{"versao_schema": "2.0.0", "id_laudo": "f3-corrompido", "data_analise": ')  # JSON truncado
    return linhas


def leituras_do_dia(n_dia: int, hora: int | None = None) -> list[dict]:
    d = _dia(n_dia)
    saida = []
    for (talhao, cultura, sensor, chave_local), dias in SENSORES.items():
        local = LOCAIS[chave_local]
        for h, (solo, temp, ur, chuva, pragas) in zip(HORAS, dias[n_dia - 1]):
            if hora is not None and h != hora:
                continue
            instante = datetime(d.year, d.month, d.day, h, 0, tzinfo=FUSO)
            saida.append({
                "id_leitura": f"sns-{sensor}-{instante:%Y%m%dT%H%M}", "instante": instante.isoformat(),
                "id_sensor": sensor, **local, "talhao": talhao, "cultura": cultura,
                "umidade_solo_pct": solo, "temperatura_c": temp, "umidade_ar_pct": ur,
                "chuva_mm": chuva, "pragas_armadilha": pragas,
            })
    return sorted(saida, key=lambda x: (x["instante"], x["id_sensor"]))


# --------------------------------------------------------------------------- #
# Publicação
# --------------------------------------------------------------------------- #
def _publicar(destino: Path, conteudo: str) -> Path:
    destino.parent.mkdir(parents=True, exist_ok=True)
    temporario = destino.with_name(f".{destino.name}.tmp")
    temporario.write_text(conteudo, encoding="utf-8")
    os.replace(temporario, destino)
    return destino


def _csv(leituras: list[dict], cabecalho: bool) -> str:
    buffer = io.StringIO()
    escritor = csv.DictWriter(buffer, fieldnames=c.NOMES_SENSOR, lineterminator="\n")
    if cabecalho:
        escritor.writeheader()
    escritor.writerows(leituras)
    return buffer.getvalue()


def publicar_laudos(pasta: Path, n_dia: int) -> Path:
    return _publicar(pasta / f"laudos_{_dia(n_dia).isoformat()}.jsonl", "\n".join(laudos_do_dia(n_dia)) + "\n")


def publicar_sensores(pasta: Path, n_dia: int, hora: int | None = None) -> Path:
    """Dia inteiro (hora=None), ou só uma hora — acrescentada ao CSV do dia."""
    destino = pasta / f"sensores_{_dia(n_dia).isoformat()}.csv"
    leituras = leituras_do_dia(n_dia, hora)
    if hora is None or not destino.exists():
        return _publicar(destino, _csv(leituras, cabecalho=True))
    with open(destino, "a", encoding="utf-8", newline="") as arquivo:
        for linha in _csv(leituras, cabecalho=False).splitlines(keepends=True):
            arquivo.write(linha)  # uma linha completa por escrita
            arquivo.flush()
    return destino


def publicar_dia(pasta: Path, n_dia: int) -> list[Path]:
    return [publicar_laudos(pasta, n_dia), publicar_sensores(pasta, n_dia)]


def reenviar(pasta: Path, n_dia: int) -> list[Path]:
    """Entrega de novo o dia N com outro nome: o monitor deve ignorar tudo (duplicados)."""
    copias = []
    for nome in (f"laudos_{_dia(n_dia).isoformat()}.jsonl", f"sensores_{_dia(n_dia).isoformat()}.csv"):
        origem = pasta / nome
        if not origem.exists():
            raise SystemExit(f"{origem} não existe — publique o dia {n_dia} antes de reenviar.")
        destino = origem.with_name(origem.stem + "_reenvio" + origem.suffix)
        temporario = destino.with_name(f".{destino.name}.tmp")
        shutil.copyfile(origem, temporario)
        os.replace(temporario, destino)
        copias.append(destino)
    return copias


def replay_fase2(pasta: Path, inicio: date, dias: int) -> list[Path]:
    """Laudos simulados da Fase 2, um arquivo por dia, na pasta monitorada."""
    if not c2.ARQUIVO_SIMULADOS.exists():
        raise SystemExit("Sem dados/simulados/laudos_simulados.jsonl — rode gerar_dados_simulados.py.")
    por_dia: dict[str, list[str]] = {}
    alvo = {(inicio + timedelta(days=k)).isoformat() for k in range(dias)}
    for linha in c2.ARQUIVO_SIMULADOS.read_text(encoding="utf-8").splitlines():
        try:
            dia = json.loads(linha).get("data_analise", "")[:10]
        except (json.JSONDecodeError, AttributeError):
            continue
        if dia in alvo:
            por_dia.setdefault(dia, []).append(linha)
    return [_publicar(pasta / f"laudos_{dia}_fase2.jsonl", "\n".join(linhas) + "\n")
            for dia, linhas in sorted(por_dia.items())]


def main() -> None:
    p = argparse.ArgumentParser(description="Publica micro-batches simulados na pasta monitorada da Fase 3.")
    grupo = p.add_mutually_exclusive_group(required=True)
    grupo.add_argument("--dia", type=int, choices=range(1, DIAS + 1), help="publica um dia do cenário")
    grupo.add_argument("--todos", action="store_true", help="publica os 7 dias de uma vez")
    grupo.add_argument("--continuo", action="store_true", help="publica dia a dia, com pausas")
    grupo.add_argument("--reenviar", type=int, choices=range(1, DIAS + 1), help="reentrega um dia já publicado")
    grupo.add_argument("--fase2", type=date.fromisoformat, metavar="AAAA-MM-DD",
                       help="reaproveita os laudos simulados da Fase 2 a partir desta data")
    p.add_argument("--dias", type=int, default=3, help="com --fase2: quantos dias")
    p.add_argument("--intervalo", type=float, default=8.0, help="com --continuo: segundos entre publicações")
    p.add_argument("--entrada", type=Path, default=c.Ambiente.padrao().entrada, help="pasta monitorada")
    a = p.parse_args()

    def mostrar(caminhos):
        for caminho in caminhos:
            linhas = sum(1 for _ in open(caminho, encoding="utf-8"))
            print(f"  publicado: {caminho.name:<38} ({linhas} linhas no arquivo)")

    if a.dia:
        mostrar(publicar_dia(a.entrada, a.dia))
    elif a.todos:
        for n in range(1, DIAS + 1):
            mostrar(publicar_dia(a.entrada, n))
    elif a.reenviar:
        mostrar(reenviar(a.entrada, a.reenviar))
    elif a.fase2:
        mostrar(replay_fase2(a.entrada, a.fase2, a.dias))
    else:
        for n in range(1, DIAS + 1):
            print(f"\n== Dia {n} ({_dia(n):%d/%m/%Y}) ==")
            mostrar([publicar_laudos(a.entrada, n)])
            for hora in HORAS:
                time.sleep(a.intervalo)
                mostrar([publicar_sensores(a.entrada, n, hora)])
                print(f"    (leituras das {hora:02d}h acrescentadas)")
            time.sleep(a.intervalo)


if __name__ == "__main__":
    main()
