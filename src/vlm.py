"""Diagnóstico por modelo de visão generalista (Google Gemini).

Complementa o CNN especializado: enquanto aquele só reconhece as 12 classes do
PlantVillage, este aceita qualquer planta — roseira, amoreira, ornamental — e
descreve o que vê em texto livre.

A saída é forçada a um schema JSON (`LaudoVLM`) para que o resultado entre na
mesma tabela de exportação do CNN, sem depender de parsing de texto solto.

Requer a variável de ambiente GEMINI_API_KEY (chave gratuita em
https://aistudio.google.com/apikey) ou a chave informada na barra lateral do app.
"""

from __future__ import annotations

import base64
import io
from datetime import datetime
from functools import lru_cache
from typing import Literal

from PIL import Image, ImageOps
from pydantic import BaseModel, Field

from . import config, rotulos
from .tipos import MOTOR_VLM, Resultado

MODELO = "gemini-3.6-flash"

# Reduzir a imagem antes de enviar corta tokens e tempo de upload sem prejudicar
# o diagnóstico: lesões foliares continuam nítidas nesta resolução.
LADO_MAXIMO = 1024
QUALIDADE_JPEG = 85

INSTRUCOES = """\
Você é um assistente de fitopatologia analisando a foto de uma planta enviada \
por um produtor rural ou jardineiro brasileiro. Responda sempre em português do \
Brasil.

Separe com rigor o que você OBSERVA do que você CONCLUI:

- `sintomas_observados` descreve apenas o que está visível na imagem — cor, \
formato e distribuição das lesões, presença de insetos, teias, pó ou mofo, \
estado do novo crescimento. Não coloque conclusões aqui.
- `diagnostico` é a conclusão tirada desses sintomas.

Regras que você não pode violar:

1. Se a imagem não mostrar uma planta, marque `e_planta` como falso e pare por aí.
2. Use `condicao: "indeterminado"` sempre que a foto não sustentar uma \
conclusão — foco ruim, planta distante demais, sintomas genéricos que servem a \
muitas causas, ou parte da planta insuficiente para julgar. É melhor admitir \
indefinição do que arriscar um laudo errado.
3. Só nomeie um patógeno ou praga específica quando os sintomas visíveis \
realmente apontarem para ele. Se a evidência for genérica, descreva o quadro \
("manchas foliares de origem não identificada") em vez de inventar uma espécie.
4. Em `manejo_recomendado`, priorize manejo cultural e biológico: poda, \
espaçamento, ventilação, irrigação, remoção de material infectado, inimigos \
naturais. Se houver indicação de controle químico, oriente a consultar um \
engenheiro agrônomo para escolha do produto e dosagem — **nunca** recomende \
princípio ativo, marca comercial ou dose por conta própria.
5. `confianca` é a sua própria avaliação da qualidade da conclusão, considerando \
nitidez da foto, clareza dos sintomas e quão distintivo é o quadro.

Seja específico e útil, mas honesto sobre os limites do que uma única foto \
permite afirmar.\
"""


class LaudoVLM(BaseModel):
    """Formato de saída exigido do modelo."""

    e_planta: bool = Field(description="A imagem mostra uma planta?")
    especie: str = Field(description="Espécie ou nome popular identificado; '—' se não identificável.")
    parte_analisada: str = Field(description="Parte visível avaliada: folha, caule, flor, fruto, planta inteira.")
    condicao: Literal["saudável", "doente", "indeterminado"]
    diagnostico: str = Field(description="Nome do problema, ou 'Saudável', ou 'Indeterminado'.")
    agente_causal: str = Field(description="Fungo, bactéria, vírus, praga, deficiência nutricional ou '—'.")
    sintomas_observados: str = Field(description="Somente o que está visível na imagem.")
    manejo_recomendado: str = Field(description="Recomendação prática de manejo.")
    confianca: Literal["baixa", "média", "alta"]
    observacoes: str = Field(description="Ressalvas, informações que faltam ou o que fotografar a seguir.")


class VLMIndisponivelError(RuntimeError):
    """Chave ausente, cota esgotada ou falha de comunicação com a API."""


@lru_cache(maxsize=4)
def _cliente(chave: str | None = None):
    """Cria o cliente uma vez por chave.

    Sem chave explícita, cai no `.env` / variável de ambiente resolvidos por
    `src/config.py`.
    """
    try:
        from google import genai
    except ImportError as erro:
        raise VLMIndisponivelError(
            "Pacote google-genai não instalado. Rode: "
            "py -3.12 -m pip install -r requirements.txt"
        ) from erro

    chave = chave or config.chave_gemini()
    if not chave:
        raise VLMIndisponivelError(
            "Chave do Gemini não encontrada. Crie um arquivo `.env` na raiz do "
            "projeto com a linha GEMINI_API_KEY=sua-chave (há um modelo pronto "
            "em `.env.example`), ou informe a chave na barra lateral do app. "
            "Chave gratuita em https://aistudio.google.com/apikey"
        )

    try:
        return genai.Client(api_key=chave)
    except Exception as erro:
        raise VLMIndisponivelError(
            f"Não foi possível iniciar o cliente Gemini: {erro}"
        ) from erro


def _preparar_imagem(imagem: Image.Image) -> str:
    """Reduz, converte para JPEG e devolve em base64, como a API espera."""
    imagem = ImageOps.exif_transpose(imagem).convert("RGB")
    imagem.thumbnail((LADO_MAXIMO, LADO_MAXIMO), Image.LANCZOS)

    buffer = io.BytesIO()
    imagem.save(buffer, format="JPEG", quality=QUALIDADE_JPEG)
    return base64.b64encode(buffer.getvalue()).decode("utf-8")


def _para_resultado(nome_arquivo: str, laudo: LaudoVLM) -> Resultado:
    agora = datetime.now().isoformat(timespec="seconds")

    if not laudo.e_planta:
        return Resultado(
            arquivo=nome_arquivo,
            data_hora=agora,
            motor=MOTOR_VLM,
            especie="—",
            diagnostico="Não é uma planta",
            condicao=rotulos.CONDICAO_INDETERMINADA,
            confianca=None,
            confianca_texto="—",
            agente="—",
            sintomas=laudo.sintomas_observados or "A imagem não mostra material vegetal.",
            manejo="Envie uma foto da planta que deseja analisar.",
            modelo_versao=MODELO,
            observacoes=laudo.observacoes,
        )

    return Resultado(
        arquivo=nome_arquivo,
        data_hora=agora,
        motor=MOTOR_VLM,
        especie=laudo.especie,
        diagnostico=laudo.diagnostico,
        condicao=laudo.condicao,
        confianca=None,
        confianca_texto=f"{laudo.confianca} (autoavaliada)",
        agente=laudo.agente_causal,
        sintomas=laudo.sintomas_observados,
        manejo=laudo.manejo_recomendado,
        modelo_versao=MODELO,
        observacoes=" · ".join(filter(None, [f"Parte analisada: {laudo.parte_analisada}", laudo.observacoes])),
    )


def analisar(nome_arquivo: str, imagem: Image.Image, chave: str | None = None) -> Resultado:
    """Envia uma imagem ao Gemini e devolve o laudo no formato padrão."""
    cliente = _cliente(chave)

    try:
        interacao = cliente.interactions.create(
            model=MODELO,
            input=[
                {"type": "text", "text": INSTRUCOES},
                {"type": "image", "data": _preparar_imagem(imagem), "mime_type": "image/jpeg"},
            ],
            response_format={
                "type": "text",
                "mime_type": "application/json",
                "schema": LaudoVLM.model_json_schema(),
            },
        )
    except VLMIndisponivelError:
        raise
    except Exception as erro:
        raise VLMIndisponivelError(
            f"Falha ao consultar o Gemini: {erro}\n"
            "Causas comuns: chave inválida, limite diário da camada gratuita "
            "atingido ou falta de conexão."
        ) from erro

    try:
        laudo = LaudoVLM.model_validate_json(interacao.output_text)
    except Exception as erro:
        raise VLMIndisponivelError(
            f"O Gemini respondeu fora do formato esperado: {erro}"
        ) from erro

    return _para_resultado(nome_arquivo, laudo)
