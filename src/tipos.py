"""Tipos compartilhados pelos dois motores de diagnóstico.

O AgroSmart tem dois motores com forças opostas:

* **CNN especializado** — MobileNetV2 treinado em 12 classes do PlantVillage.
  Rápido, offline, gratuito e com acurácia medida — mas só conhece 5 culturas.
* **VLM (Gemini)** — modelo de visão generalista. Reconhece qualquer planta e
  descreve o que vê em texto livre, ao custo de uma chamada de API e sem
  acurácia mensurável por classe.

Ambos produzem o mesmo `Resultado`, para que a exportação e a interface não
precisem saber qual motor respondeu.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import rotulos

MOTOR_CNN = "CNN especializado"
MOTOR_VLM = "Gemini (VLM)"


@dataclass(frozen=True)
class Predicao:
    """Uma posição do ranking de probabilidades do CNN."""

    id_classe: str
    confianca: float

    @property
    def rotulo(self) -> str:
        info = rotulos.descrever(self.id_classe)
        return f"{info['cultura']} — {info['diagnostico']}"


@dataclass(frozen=True)
class Resultado:
    """Diagnóstico de uma imagem, pronto para exibição e exportação.

    Sobre os dois campos de confiança: o CNN devolve uma probabilidade real
    (softmax sobre 12 classes) e o VLM devolve apenas uma autoavaliação em
    palavras. Misturar as duas coisas numa única coluna numérica daria à
    autoavaliação uma precisão que ela não tem, então `confianca` fica em None
    nas linhas do VLM e `confianca_texto` carrega o rótulo legível dos dois.
    """

    arquivo: str
    data_hora: str
    motor: str
    especie: str
    diagnostico: str
    condicao: str
    confianca: float | None
    confianca_texto: str
    agente: str
    sintomas: str
    manejo: str
    modelo_versao: str
    observacoes: str = ""
    ranking: tuple[Predicao, ...] = field(default=())

    @property
    def indeterminado(self) -> bool:
        return self.condicao == rotulos.CONDICAO_INDETERMINADA

    @property
    def do_cnn(self) -> bool:
        return self.motor == MOTOR_CNN
