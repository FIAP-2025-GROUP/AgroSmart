"""Carregamento do modelo CNN e classificação de imagens.

Este módulo não depende do Streamlit de propósito: o mesmo código serve ao app
(`app.py`) e ao classificador de pasta em lote (`classificar_pasta.py`).

Contrato com o notebook de treino
---------------------------------
O modelo espera imagens RGB de 224x224 com valores brutos em 0..255. A
normalização para o intervalo [-1, 1] exigida pelo MobileNetV2 acontece dentro
do próprio modelo, numa camada `Rescaling` — assim é impossível o pré-processo
do treino divergir do pré-processo da inferência, que é a origem mais comum de
"funciona no notebook, erra no app".
"""

from __future__ import annotations

import json
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Sequence

import numpy as np
from PIL import Image, ImageOps

from . import rotulos
from .tipos import MOTOR_CNN, Predicao, Resultado

RAIZ = Path(__file__).resolve().parent.parent
CAMINHO_MODELO = RAIZ / "modelo" / "agrosmart_mobilenetv2.keras"
CAMINHO_CLASSES = RAIZ / "modelo" / "classes.json"

TAMANHO_ENTRADA = (224, 224)
QUANTIDADE_ALTERNATIVAS = 3


class ModeloIndisponivelError(RuntimeError):
    """O modelo treinado não foi encontrado ou não bate com o catálogo atual."""


@lru_cache(maxsize=1)
def carregar_modelo(caminho: Path = CAMINHO_MODELO, caminho_classes: Path = CAMINHO_CLASSES):
    """Carrega o modelo e seus metadados uma única vez por processo.

    O Streamlit reexecuta o script inteiro a cada interação, mas os módulos
    importados permanecem em memória — então o `lru_cache` evita recarregar os
    ~14 MB do modelo a cada clique, sem acoplar este arquivo ao Streamlit.
    """
    if not caminho.exists():
        raise ModeloIndisponivelError(
            f"Modelo não encontrado em {caminho}.\n"
            "Rode notebooks/treino_agrosmart.ipynb no Google Colab e copie os "
            "arquivos gerados para a pasta modelo/. Para desenvolver o app sem "
            "o modelo real, gere um stub com: py -3.12 ferramentas/gerar_stub.py"
        )
    if not caminho_classes.exists():
        raise ModeloIndisponivelError(
            f"Arquivo de classes não encontrado em {caminho_classes}. "
            "Ele é gerado junto com o modelo pelo notebook de treino."
        )

    # Import tardio: o TensorFlow leva alguns segundos para carregar e não deve
    # penalizar quem usa apenas o motor VLM.
    from tensorflow import keras

    metadados = json.loads(caminho_classes.read_text(encoding="utf-8"))
    ids = metadados["ids"]

    if ids != rotulos.IDS_ORDENADOS:
        raise ModeloIndisponivelError(
            "A ordem das classes do modelo não corresponde ao catálogo em "
            "src/rotulos.py. O modelo foi treinado com outra versão do "
            f"catálogo (modelo: {metadados.get('versao_catalogo')}, "
            f"código: {rotulos.VERSAO_CATALOGO}). Retreine ou restaure o "
            "catálogo compatível."
        )

    modelo = keras.models.load_model(caminho)
    versao = f"{metadados.get('arquitetura', 'desconhecida')} v{metadados.get('versao_catalogo', '?')}"
    return modelo, ids, versao, metadados


def preparar_imagem(imagem: Image.Image) -> np.ndarray:
    """Converte uma imagem PIL no tensor de entrada do modelo.

    `exif_transpose` corrige a orientação de fotos de celular, que costumam vir
    deitadas com a rotação apenas marcada nos metadados EXIF.
    """
    imagem = ImageOps.exif_transpose(imagem)
    imagem = imagem.convert("RGB").resize(TAMANHO_ENTRADA, Image.BILINEAR)
    return np.asarray(imagem, dtype="float32")


def _montar_resultado(
    nome_arquivo: str,
    probabilidades: np.ndarray,
    ids: Sequence[str],
    versao: str,
    limiar: float,
) -> Resultado:
    ordem = np.argsort(probabilidades)[::-1][:QUANTIDADE_ALTERNATIVAS]
    ranking = tuple(
        Predicao(id_classe=ids[indice], confianca=float(probabilidades[indice]))
        for indice in ordem
    )
    melhor = ranking[0]
    agora = datetime.now().isoformat(timespec="seconds")

    if melhor.confianca < limiar:
        return Resultado(
            arquivo=nome_arquivo,
            data_hora=agora,
            motor=MOTOR_CNN,
            especie="—",
            diagnostico="Indeterminado",
            condicao=rotulos.CONDICAO_INDETERMINADA,
            confianca=melhor.confianca,
            confianca_texto=f"{melhor.confianca:.1%}",
            agente="—",
            sintomas=(
                "Imagem fora do domínio treinado ou de baixa qualidade: nenhuma "
                f"das 12 classes atingiu a confiança mínima de {limiar:.0%}."
            ),
            manejo=(
                "Esta cultura provavelmente não está entre as 12 classes do "
                "modelo especializado. Use o motor de visão generalista para "
                "analisar esta imagem."
            ),
            modelo_versao=versao,
            ranking=ranking,
        )

    info = rotulos.descrever(melhor.id_classe)
    return Resultado(
        arquivo=nome_arquivo,
        data_hora=agora,
        motor=MOTOR_CNN,
        especie=info["cultura"],
        diagnostico=info["diagnostico"],
        condicao=info["condicao"],
        confianca=melhor.confianca,
        confianca_texto=f"{melhor.confianca:.1%}",
        agente=info["agente"],
        sintomas=info["sintomas"],
        manejo=info["manejo"],
        modelo_versao=versao,
        ranking=ranking,
    )


def predizer_lote(
    itens: Sequence[tuple[str, Image.Image]],
    limiar: float = rotulos.LIMIAR_CONFIANCA,
) -> list[Resultado]:
    """Classifica várias imagens numa única passada pela rede.

    `itens` é uma sequência de pares (nome do arquivo, imagem PIL). Empilhar
    tudo num só batch é bem mais rápido do que chamar o modelo N vezes.
    """
    if not itens:
        return []

    modelo, ids, versao, _ = carregar_modelo()
    lote = np.stack([preparar_imagem(imagem) for _, imagem in itens])
    probabilidades = modelo.predict(lote, verbose=0)

    return [
        _montar_resultado(nome, probabilidades[posicao], ids, versao, limiar)
        for posicao, (nome, _) in enumerate(itens)
    ]


def predizer(nome: str, imagem: Image.Image, limiar: float = rotulos.LIMIAR_CONFIANCA) -> Resultado:
    """Conveniência para uma imagem só."""
    return predizer_lote([(nome, imagem)], limiar=limiar)[0]
