# AgroSmart — imagem para Hugging Face Spaces (SDK Docker) ou qualquer host de contêiner.
# O Space escuta na porta 7860 e roda o contêiner como usuário 1000.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    TF_CPP_MIN_LOG_LEVEL=2 \
    PORT=7860

# Usuário não-root exigido pelo Hugging Face Spaces.
RUN useradd -m -u 1000 usuario
WORKDIR /home/usuario/app

# Dependências primeiro (camada em cache). Troca tensorflow por tensorflow-cpu
# (imagem bem menor) e tira o streamlit, que só serve ao app.py de reserva.
COPY requirements.txt .
RUN sed -e 's/^tensorflow>=/tensorflow-cpu>=/' -e '/^streamlit/d' requirements.txt > req-deploy.txt \
    && pip install --no-cache-dir -r req-deploy.txt

COPY --chown=usuario:usuario . .
RUN mkdir -p dados exportacoes && chown -R usuario:usuario dados exportacoes
USER usuario

EXPOSE 7860
CMD ["sh", "-c", "python servidor.py --publico --porta ${PORT}"]
