"""Carregamento de configuração a partir do arquivo `.env`.

Importar este módulo já carrega o `.env` da raiz do projeto para as variáveis de
ambiente. `src/vlm.py` o importa, então qualquer ponto de entrada — app,
classificador de pasta ou um script solto — recebe a chave sem precisar
lembrar de chamar nada.

Variáveis já definidas no ambiente do sistema têm precedência sobre o `.env`,
que é o comportamento padrão do python-dotenv e o que se espera em produção.
"""

from __future__ import annotations

import os
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
CAMINHO_ENV = RAIZ / ".env"

VARIAVEL_GEMINI = "GEMINI_API_KEY"

# Registrado antes do load para distinguir a origem da chave na interface.
_VEIO_DO_SISTEMA = bool(os.environ.get(VARIAVEL_GEMINI))

try:
    from dotenv import load_dotenv

    load_dotenv(CAMINHO_ENV)
except ImportError:  # pragma: no cover - só ocorre com dependências incompletas
    pass


# Valor que vem no .env.example. Tratado como ausente para que copiar o modelo
# sem preencher não faça o app anunciar uma chave que não existe e só falhar na
# primeira chamada à API.
PLACEHOLDER = "cole-sua-chave-aqui"


def chave_gemini() -> str | None:
    """A chave da API, venha ela do ambiente ou do `.env`. None se ausente."""
    valor = (os.environ.get(VARIAVEL_GEMINI) or "").strip()
    return valor or None if valor != PLACEHOLDER else None


def origem_chave() -> str | None:
    """De onde a chave foi lida, para exibir na interface."""
    if not chave_gemini():
        return None
    if _VEIO_DO_SISTEMA:
        return "variável de ambiente do sistema"
    return "arquivo `.env`" if CAMINHO_ENV.exists() else "ambiente"
