"""API do AgroSmart — serve o frontend e expõe a análise de imagens.

Execução:
    py -3.12 servidor.py
    py -3.12 servidor.py --porta 8080 --publico

Toda a lógica de diagnóstico vive em `src/`; este arquivo é só a camada HTTP.
"""

from __future__ import annotations

import argparse
import io
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from PIL import Image, UnidentifiedImageError

from src import config, diagnostico, exportacao, historico, inferencia, rotulos, vlm
from src.tipos import Resultado

RAIZ = Path(__file__).resolve().parent
PASTA_WEB = RAIZ / "web"

app = FastAPI(title="AgroSmart", docs_url="/api/docs", redoc_url=None)


@app.middleware("http")
async def revalidar_frontend(requisicao, proximo):
    """Impede que o navegador sirva HTML/CSS/JS antigos depois de uma edição.

    `no-cache` não desliga o cache: obriga a revalidar. Como o StaticFiles
    manda ETag, o arquivo inalterado volta como 304 sem trafegar bytes — mas
    o alterado chega na hora, sem exigir recarga forçada do usuário.
    """
    resposta = await proximo(requisicao)
    caminho = requisicao.url.path
    if caminho == "/" or caminho.startswith("/web/"):
        resposta.headers["Cache-Control"] = "no-cache"
    return resposta


# --------------------------------------------------------------------------- #
# Serialização
# --------------------------------------------------------------------------- #
def _serializar(resultado: Resultado, id_historico: int | None = None) -> dict[str, Any]:
    return {
        "id_historico": id_historico,
        "arquivo": resultado.arquivo,
        "data_hora": resultado.data_hora,
        "motor": resultado.motor,
        "especie": resultado.especie,
        "diagnostico": resultado.diagnostico,
        "condicao": resultado.condicao,
        "confianca": resultado.confianca,
        "confianca_texto": resultado.confianca_texto,
        "agente": resultado.agente,
        "sintomas": resultado.sintomas,
        "manejo": resultado.manejo,
        "observacoes": resultado.observacoes,
        "modelo_versao": resultado.modelo_versao,
        "ranking": [
            {"rotulo": p.rotulo, "confianca": p.confianca} for p in resultado.ranking
        ],
    }


# --------------------------------------------------------------------------- #
# Estado
# --------------------------------------------------------------------------- #
@app.get("/api/estado")
def estado() -> dict[str, Any]:
    """O que o frontend precisa saber ao carregar: motores prontos e modos."""
    try:
        inferencia.carregar_modelo()
        cnn_ok, cnn_detalhe = True, "modelo carregado"
    except Exception as erro:
        cnn_ok, cnn_detalhe = False, str(erro).split("\n")[0]

    return {
        "cnn_disponivel": cnn_ok,
        "cnn_detalhe": cnn_detalhe,
        "vlm_disponivel": bool(config.chave_gemini()),
        "vlm_origem_chave": config.origem_chave(),
        "vlm_modelo": vlm.MODELO,
        "modos": diagnostico.MODOS,
        "modo_padrao": diagnostico.MODO_AUTOMATICO,
        "limiar_padrao": rotulos.LIMIAR_CONFIANCA,
        "classes_cnn": [
            {
                "cultura": c["cultura"],
                "diagnostico": c["diagnostico"],
                "condicao": c["condicao"],
            }
            for c in rotulos.CLASSES
        ],
        "total_historico": historico.total(),
    }


# --------------------------------------------------------------------------- #
# Análise
# --------------------------------------------------------------------------- #
@app.post("/api/analisar")
async def analisar(
    imagem: UploadFile = File(...),
    modo: str = Form(diagnostico.MODO_AUTOMATICO),
    limiar: float = Form(rotulos.LIMIAR_CONFIANCA),
    salvar: bool = Form(True),
) -> JSONResponse:
    """Analisa uma imagem e, por padrão, grava o laudo no histórico."""
    if modo not in diagnostico.MODOS:
        raise HTTPException(400, f"Modo desconhecido: {modo}")

    conteudo = await imagem.read()
    if not conteudo:
        raise HTTPException(400, "Arquivo vazio.")

    try:
        figura = Image.open(io.BytesIO(conteudo))
        figura.load()
    except (UnidentifiedImageError, OSError):
        raise HTTPException(400, "Não foi possível ler a imagem. Envie PNG ou JPEG.")

    nome = imagem.filename or "imagem.jpg"

    try:
        resultados, avisos = diagnostico.analisar_lote(
            [(nome, figura)], modo=modo, limiar=limiar
        )
    except (vlm.VLMIndisponivelError, inferencia.ModeloIndisponivelError) as erro:
        raise HTTPException(503, str(erro))
    except Exception as erro:
        raise HTTPException(500, f"{type(erro).__name__}: {erro}")

    if not resultados:
        raise HTTPException(500, "Nenhum laudo foi produzido para esta imagem.")

    laudos = []
    for resultado in resultados:
        id_historico = historico.salvar(resultado, figura) if salvar else None
        laudos.append(_serializar(resultado, id_historico))

    return JSONResponse({"laudos": laudos, "avisos": avisos})


# --------------------------------------------------------------------------- #
# Histórico
# --------------------------------------------------------------------------- #
@app.get("/api/historico")
def listar_historico(limite: int = 60) -> dict[str, Any]:
    return {"itens": historico.listar(limite), "total": historico.total()}


@app.get("/api/historico/{id_laudo}")
def obter_laudo(id_laudo: int) -> dict[str, Any]:
    laudo = historico.obter(id_laudo)
    if laudo is None:
        raise HTTPException(404, "Laudo não encontrado.")
    return laudo


@app.get("/api/historico/{id_laudo}/miniatura")
def obter_miniatura(id_laudo: int) -> Response:
    dados = historico.miniatura(id_laudo)
    if dados is None:
        raise HTTPException(404, "Miniatura não encontrada.")
    return Response(dados, media_type="image/jpeg", headers={"Cache-Control": "max-age=86400"})


@app.delete("/api/historico/{id_laudo}")
def remover_laudo(id_laudo: int) -> dict[str, Any]:
    if not historico.remover(id_laudo):
        raise HTTPException(404, "Laudo não encontrado.")
    return {"removido": id_laudo, "total": historico.total()}


@app.delete("/api/historico")
def limpar_historico() -> dict[str, Any]:
    return {"removidos": historico.limpar(), "total": 0}


# --------------------------------------------------------------------------- #
# Exportação
# --------------------------------------------------------------------------- #
@app.get("/api/exportar/{formato}")
def exportar(formato: str) -> Response:
    """Exporta todo o histórico. `formato` é `csv` ou `json`."""
    resultados = historico.como_resultados()
    if not resultados:
        raise HTTPException(404, "Histórico vazio — não há o que exportar.")

    if formato == "csv":
        return Response(
            exportacao.gerar_csv(resultados),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="agrosmart_historico.csv"'},
        )
    if formato == "json":
        return Response(
            exportacao.gerar_json(resultados),
            media_type="application/json; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="agrosmart_historico.json"'},
        )
    raise HTTPException(400, "Formato deve ser csv ou json.")


# --------------------------------------------------------------------------- #
# Frontend
# --------------------------------------------------------------------------- #
@app.get("/")
def raiz() -> FileResponse:
    return FileResponse(PASTA_WEB / "index.html")


app.mount("/web", StaticFiles(directory=PASTA_WEB), name="web")


def main() -> None:
    analisador = argparse.ArgumentParser(description="Servidor do AgroSmart.")
    analisador.add_argument("--porta", type=int, default=8000)
    analisador.add_argument(
        "--publico",
        action="store_true",
        help="Escuta em 0.0.0.0 para abrir de outro aparelho na mesma rede.",
    )
    argumentos = analisador.parse_args()

    import uvicorn

    host = "0.0.0.0" if argumentos.publico else "127.0.0.1"
    print(f"\n  AgroSmart em http://localhost:{argumentos.porta}\n")
    uvicorn.run(app, host=host, port=argumentos.porta, log_level="warning")


if __name__ == "__main__":
    main()
