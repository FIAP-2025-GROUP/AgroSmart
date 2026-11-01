"""Contrato da Fase 3: formatos de entrada, limites das regras e caminhos.

Dois tipos de registro chegam à pasta monitorada (`dados/entrada/`):

* **laudo** — o mesmo contrato da Fase 2 (`src/pipeline/contratos.py`,
  versão 2.0.0), um por linha em arquivos `laudos_*.jsonl`. É o que o app de
  diagnóstico (Fase 1) produz e o pipeline da Fase 2 consolida.
* **leitura de sensor** — uma medição de campo por talhão (umidade do solo,
  temperatura, umidade do ar, chuva e contagem da armadilha de pragas), em
  arquivos `sensores_*.csv` (ou `sensores_*.jsonl`).

O tipo é decidido pelo prefixo do nome do arquivo. Arquivos que começam com
`.` ou `~`, ou terminam em `.tmp`/`.part`, são ignorados: é assim que o produtor
escreve sem que o monitor leia um arquivo pela metade (grava `.x.tmp` e renomeia).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent.parent

VERSAO_REGRAS = "3.0.0"

TIPO_LAUDO = "laudo"
TIPO_SENSOR = "sensor"
PREFIXOS = {"laudos_": TIPO_LAUDO, "sensores_": TIPO_SENSOR}
EXTENSOES = {".jsonl", ".csv"}

# --------------------------------------------------------------------------- #
# Leitura de sensor
# --------------------------------------------------------------------------- #
# (nome, tipo, unidade, obrigatório, faixa válida, exemplo, descrição)
CAMPOS_SENSOR: list[tuple] = [
    ("id_leitura", "texto", "—", True, None, "sns-T02-20261005T0600",
     "identificador único da leitura (chave de deduplicação)"),
    ("instante", "texto ISO-8601", "data/hora com fuso", True, None, "2026-10-05T06:00:00-03:00",
     "momento da medição; sem fuso = horário de Brasília"),
    ("id_sensor", "texto", "—", True, None, "est-sbe-T02", "estação de campo que mediu"),
    ("uf", "texto", "sigla", False, None, "SP", "unidade da federação"),
    ("municipio", "texto", "—", False, None, "Campinas", "município"),
    ("propriedade", "texto", "—", True, None, "Sítio Boa Esperança", "propriedade rural"),
    ("talhao", "texto", "—", True, None, "T02", "talhão dentro da propriedade"),
    ("cultura", "texto", "—", True, None, "Tomate", "cultura plantada no talhão"),
    ("umidade_solo_pct", "decimal", "% volumétrica", True, (0, 100), 22.4,
     "umidade do solo a 20 cm"),
    ("temperatura_c", "decimal", "°C", True, (-10, 55), 18.6, "temperatura do ar"),
    ("umidade_ar_pct", "decimal", "% UR", True, (0, 100), 93.0, "umidade relativa do ar"),
    ("chuva_mm", "decimal", "mm", True, (0, 300), 0.0, "chuva acumulada desde a leitura anterior"),
    ("pragas_armadilha", "inteiro", "insetos/armadilha", True, (0, 10000), 4,
     "contagem na armadilha adesiva do talhão"),
]
NOMES_SENSOR = [campo[0] for campo in CAMPOS_SENSOR]
NUMERICOS_SENSOR = {campo[0]: campo[4] for campo in CAMPOS_SENSOR if campo[4] is not None}
OBRIGATORIOS_SENSOR = [campo[0] for campo in CAMPOS_SENSOR if campo[3]]

# --------------------------------------------------------------------------- #
# Limites das regras (um lugar só — o documento de regras cita estes valores)
# --------------------------------------------------------------------------- #
LIMITES = {
    # R1 — infestação por diagnóstico de imagem
    "r1_janela_dias": 7,
    "r1_min_analises": 5,
    "r1_pct_doente": 0.30,
    "r1_carencia_dias": 7,
    # R2 — mesmo problema em dias seguidos
    "r2_dias_seguidos": 3,
    "r2_carencia_dias": 14,
    # R3 — irrigação (histerese)
    "r3_liga_abaixo_pct": 25.0,
    "r3_desliga_acima_pct": 35.0,
    "r3_critico_abaixo_pct": 15.0,
    "r3_chuva_suficiente_mm": 5.0,
    "r3_janela_chuva_horas": 24,
    # R4 — clima favorável à requeima
    "r4_culturas": ("Tomate", "Batata"),
    "r4_ur_min_pct": 90.0,
    "r4_temp_min_c": 10.0,
    "r4_temp_max_c": 25.0,
    "r4_leituras_seguidas": 2,
    "r4_carencia_horas": 24,
    # R5 — pragas na armadilha
    "r5_nivel_controle": 20,
    "r5_nivel_atencao": 10,
    "r5_janela_laudo_dias": 3,
    "r5_carencia_horas": 24,
}


# --------------------------------------------------------------------------- #
# Caminhos
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Ambiente:
    """Onde o monitor lê e grava. Os testes passam uma pasta temporária."""

    entrada: Path
    base: Path  # estado + saídas

    @classmethod
    def padrao(cls) -> "Ambiente":
        """`dados/entrada` e `dados/fase3`, ou AGROSMART_ENTRADA / AGROSMART_FASE3 se definidas."""
        return cls(entrada=Path(os.environ.get("AGROSMART_ENTRADA") or RAIZ / "dados" / "entrada"),
                   base=Path(os.environ.get("AGROSMART_FASE3") or RAIZ / "dados" / "fase3"))

    @property
    def banco(self) -> Path:
        return self.base / "estado.db"

    @property
    def saida(self) -> Path:
        return self.base / "saida"

    @property
    def emails(self) -> Path:
        return self.saida / "emails"

    @property
    def relatorios(self) -> Path:
        return self.saida / "relatorios"

    @property
    def alertas(self) -> Path:
        return self.saida / "alertas"

    @property
    def irrigacao(self) -> Path:
        return self.saida / "irrigacao"

    @property
    def logs(self) -> Path:
        return self.base / "logs"

    def criar_pastas(self) -> None:
        for pasta in (self.entrada, self.base, self.emails, self.relatorios, self.alertas,
                      self.irrigacao, self.logs):
            pasta.mkdir(parents=True, exist_ok=True)


def tipo_do_arquivo(nome: str) -> str | None:
    """`laudo`, `sensor` ou None (arquivo que o monitor não deve ler)."""
    if nome.startswith((".", "~")) or nome.endswith((".tmp", ".part")):
        return None
    if Path(nome).suffix.lower() not in EXTENSOES:
        return None
    for prefixo, tipo in PREFIXOS.items():
        if nome.startswith(prefixo):
            return tipo
    return None
