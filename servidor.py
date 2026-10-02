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

from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
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
    if caminho in ("/", "/dashboard") or caminho.startswith("/web/"):
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
    uf: str | None = Form(None),
    municipio: str | None = Form(None),
    propriedade: str | None = Form(None),
    talhao: str | None = Form(None),
) -> JSONResponse:
    """Analisa uma imagem e, por padrão, grava o laudo no histórico.

    Ao salvar, todos os laudos do envio ficam ligados a uma mesma análise
    (`id_analise`), com o SHA-256 da imagem, o modo, o limiar aplicado e a
    localidade opcional — é o que permite ao painel contar análises reais.
    """
    if modo not in diagnostico.MODOS:
        raise HTTPException(400, f"Modo desconhecido: {modo}")
    try:
        contexto = historico.normalizar_contexto(
            {"uf": uf, "municipio": municipio, "propriedade": propriedade, "talhao": talhao})
    except ValueError as erro:
        raise HTTPException(400, str(erro))

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

    id_analise, ids = None, [None] * len(resultados)
    if salvar:
        # O limiar só foi aplicado se o CNN rodou: não no modo só VLM, nem
        # quando o modelo estava indisponível e só o generalista respondeu.
        cnn_rodou = modo != diagnostico.MODO_VLM and not any(
            aviso.startswith("Motor CNN indisponível") for aviso in avisos)
        id_analise, ids = historico.salvar_analise(
            resultados, figura, conteudo, nome, modo,
            limiar if cnn_rodou else None, contexto,
        )

    laudos = [_serializar(resultado, id_historico) for resultado, id_historico in zip(resultados, ids)]
    return JSONResponse({"laudos": laudos, "avisos": avisos, "id_analise": id_analise})


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
# Painel analítico (Fase 2)
# --------------------------------------------------------------------------- #
@app.get("/api/analitica")
def painel_analitico(
    origem: str | None = Query(None, description="real, simulado ou todas"),
    inicio: str | None = Query(None, description="AAAA-MM-DD"),
    fim: str | None = Query(None, description="AAAA-MM-DD"),
    uf: str | None = None,
    municipio: str | None = None,
    propriedade: str | None = None,
    talhao: str | None = None,
    cultura: str | None = None,
    condicao: str | None = None,
    categoria: str | None = None,
    motor: str | None = None,
    pagina: int = 1,
) -> dict[str, Any]:
    """Cards, gráficos, tabela e qualidade do pacote processado, já filtrados.

    Lê somente o pacote publicado pelo pipeline (`dados/processados/`); nunca
    o SQLite operacional nem o Spark. O import é tardio de propósito: sem a
    camada analítica, só esta rota responde 503 — o diagnóstico segue intacto.
    """
    try:
        from src import analitica
    except ImportError as erro:
        raise HTTPException(503, f"Camada analítica indisponível: {erro}")

    filtros = {
        "origem": origem, "inicio": inicio, "fim": fim, "uf": uf, "municipio": municipio,
        "propriedade": propriedade, "talhao": talhao, "cultura": cultura,
        "condicao": condicao, "categoria": categoria, "motor": motor,
    }
    try:
        return analitica.consultar(filtros, pagina=pagina)
    except ValueError as erro:
        raise HTTPException(400, str(erro))
    except Exception as erro:  # pacote externo nunca derruba o servidor com 500
        raise HTTPException(503, f"Camada analítica indisponível: {type(erro).__name__}: {erro}")


# --------------------------------------------------------------------------- #
# Frontend
# --------------------------------------------------------------------------- #
@app.get("/")
def raiz() -> FileResponse:
    return FileResponse(PASTA_WEB / "index.html")


@app.get("/dashboard")
def dashboard() -> FileResponse:
    pagina = PASTA_WEB / "dashboard.html"
    if not pagina.exists():
        raise HTTPException(404, "Painel analítico não instalado.")
    return FileResponse(pagina)


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
