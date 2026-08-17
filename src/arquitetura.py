"""Definição da rede AgroSmart.

Este arquivo é a referência da arquitetura. A célula equivalente do notebook
`notebooks/treino_agrosmart.ipynb` repete este código propositalmente, para que
o notebook seja autossuficiente no Colab — se alterar um, altere o outro.
"""

from __future__ import annotations

TAMANHO_ENTRADA = (224, 224)


def construir_modelo(quantidade_classes: int, base_treinavel: bool = False):
    """MobileNetV2 pré-treinado no ImageNet com um classificador novo no topo.

    A camada `Rescaling(1/127.5, offset=-1)` reproduz exatamente o
    `preprocess_input` do MobileNetV2, mas fica *dentro* do modelo. Assim o
    treino e a inferência recebem os mesmos pixels brutos em 0..255 e não há
    como as duas normalizações divergirem.
    """
    from tensorflow import keras
    from tensorflow.keras import layers

    base = keras.applications.MobileNetV2(
        input_shape=(*TAMANHO_ENTRADA, 3),
        include_top=False,
        weights="imagenet",
    )
    base.trainable = base_treinavel

    entradas = keras.Input(shape=(*TAMANHO_ENTRADA, 3), name="imagem")
    x = layers.Rescaling(1 / 127.5, offset=-1, name="normalizacao")(entradas)
    x = base(x, training=False)
    x = layers.GlobalAveragePooling2D(name="pooling")(x)
    x = layers.Dropout(0.3, name="dropout")(x)
    saidas = layers.Dense(quantidade_classes, activation="softmax", name="diagnostico")(x)

    return keras.Model(entradas, saidas, name="agrosmart_mobilenetv2")
