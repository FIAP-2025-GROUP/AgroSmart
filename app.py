"""AgroSmart — Fase 1: diagnóstico de sanidade vegetal por visão computacional.

Execução:  streamlit run app.py
"""

from __future__ import annotations

import html
import traceback
from collections import defaultdict
from pathlib import Path

import streamlit as st
from PIL import Image

from src import config, diagnostico, exportacao, rotulos
from src.tipos import MOTOR_CNN

RAIZ = Path(__file__).resolve().parent
PASTA_EXPORTACOES = RAIZ / "exportacoes"

CORES_CONDICAO = {
    rotulos.CONDICAO_SAUDAVEL: ("#1a7f37", "#dafbe1"),
    rotulos.CONDICAO_DOENTE: ("#a40e26", "#ffebe9"),
    rotulos.CONDICAO_INDETERMINADA: ("#4d5560", "#eef1f4"),
}

st.set_page_config(page_title="AgroSmart — Diagnóstico de Plantas", page_icon="🌿", layout="wide")


def badge(texto: str, condicao: str) -> str:
    """Selo colorido de condição. O texto é escapado por vir de dado externo."""
    cor_texto, cor_fundo = CORES_CONDICAO.get(condicao, CORES_CONDICAO[rotulos.CONDICAO_INDETERMINADA])
    return (
        f'<span style="background:{cor_fundo};color:{cor_texto};padding:2px 10px;'
        f'border-radius:12px;font-size:0.78rem;font-weight:600;white-space:nowrap;">'
        f"{html.escape(texto)}</span>"
    )


def assinatura(arquivos, modo: str, limiar: float) -> tuple:
    """Identifica o lote atual para não reanalisar a cada rerun do Streamlit.

    O Streamlit reexecuta o script inteiro a cada interação — inclusive ao
    clicar num botão de download. Sem esta checagem, baixar o CSV dispararia
    uma nova rodada de chamadas pagas à API.
    """
    return (tuple((arquivo.name, arquivo.size) for arquivo in arquivos), modo, limiar)


# --------------------------------------------------------------------------- #
# Barra lateral
# --------------------------------------------------------------------------- #
with st.sidebar:
    st.header("Configuração")

    modo = st.radio(
        "Motor de diagnóstico",
        options=diagnostico.MODOS,
        help=(
            "O CNN especializado é rápido, gratuito e roda offline, mas só "
            "conhece 12 condições em 5 culturas. O generalista reconhece "
            "qualquer planta, ao custo de uma chamada de API."
        ),
    )

    limiar = st.slider(
        "Confiança mínima do CNN",
        min_value=0.30,
        max_value=0.95,
        value=rotulos.LIMIAR_CONFIANCA,
        step=0.05,
        help=(
            "Abaixo deste valor o CNN se declara indeterminado. No modo "
            "automático, é o que passa a imagem para o motor generalista."
        ),
    )

    precisa_chave = modo != diagnostico.MODO_CNN
    chave = config.chave_gemini() or ""

    if precisa_chave:
        st.divider()
        st.subheader("Chave do Gemini")
        if chave:
            st.success(f"Chave carregada do {config.origem_chave()}.")
        else:
            chave = st.text_input(
                "Chave da API",
                type="password",
                help="Chave gratuita em aistudio.google.com/apikey",
                placeholder="AIza…",
            )
            if chave:
                st.caption(
                    "Vale só nesta sessão do navegador. Para não digitar de "
                    "novo, copie `.env.example` para `.env` e coloque a chave lá."
                )
            else:
                st.warning(
                    "Sem chave, só o motor CNN funciona. Copie `.env.example` "
                    "para `.env` e preencha `GEMINI_API_KEY`, ou cole a chave "
                    "no campo acima."
                )
        st.caption("O arquivo `.env` está no .gitignore e nunca entra nas exportações.")

    st.divider()
    with st.expander("Classes do CNN especializado"):
        for classe in rotulos.CLASSES:
            st.markdown(
                f"{badge(classe['condicao'], classe['condicao'])} "
                f"**{classe['cultura']}** — {classe['diagnostico']}",
                unsafe_allow_html=True,
            )
    st.caption(
        "Fora desta lista, só o motor generalista consegue diagnosticar — é o "
        "caso de roseiras, ornamentais e frutíferas não treinadas."
    )


# --------------------------------------------------------------------------- #
# Cabeçalho e entrada
# --------------------------------------------------------------------------- #
st.title("🌿 AgroSmart — Diagnóstico de Plantas")
st.caption(
    "Envie fotos de qualquer planta. O sistema identifica a espécie, classifica "
    "como saudável ou doente, aponta o agente causal e recomenda o manejo."
)

arquivos = st.file_uploader(
    "Imagens para análise",
    type=["png", "jpg", "jpeg"],
    accept_multiple_files=True,
    help="Enquadre a folha ou o ramo afetado, com boa iluminação.",
)

if not arquivos:
    st.info(
        "Nenhuma imagem carregada ainda. Fotos de celular funcionam bem — "
        "aproxime da parte afetada e evite sol direto."
    )
    st.stop()


# --------------------------------------------------------------------------- #
# Análise
# --------------------------------------------------------------------------- #
chave_lote = assinatura(arquivos, modo, limiar)

if st.session_state.get("chave_lote") != chave_lote:
    barra = st.progress(0.0, text="Preparando…")

    def atualizar(feitos: int, total: int, nome: str) -> None:
        barra.progress(feitos / total, text=f"Analisando {nome} ({feitos}/{total})")

    try:
        itens = [(arquivo.name, Image.open(arquivo)) for arquivo in arquivos]
        resultados, avisos = diagnostico.analisar_lote(
            itens, modo=modo, limiar=limiar, chave_vlm=chave or None, progresso=atualizar
        )
        st.session_state["resultados"] = resultados
        st.session_state["avisos"] = avisos
        st.session_state["chave_lote"] = chave_lote
    except Exception as erro:  # VLM indisponível, modelo CNN ausente e afins
        barra.empty()
        # Algumas exceções (inclusive as internas do Streamlit) têm str() vazia
        # e renderizariam uma caixa de erro em branco. O tipo sempre aparece.
        mensagem = str(erro).strip() or "(sem mensagem)"
        st.error(f"**{type(erro).__name__}** — {mensagem}")
        with st.expander("Detalhes técnicos"):
            st.code(traceback.format_exc(), language="text")
        st.stop()
    finally:
        barra.empty()

resultados = st.session_state["resultados"]
for aviso in st.session_state.get("avisos", []):
    st.warning(aviso)

if not resultados:
    st.error("Nenhum laudo foi produzido para este lote.")
    st.stop()


# --------------------------------------------------------------------------- #
# Resumo do lote
# --------------------------------------------------------------------------- #
por_arquivo: dict[str, list] = defaultdict(list)
for resultado in resultados:
    por_arquivo[resultado.arquivo].append(resultado)

doentes = sum(1 for r in resultados if r.condicao == rotulos.CONDICAO_DOENTE)
saudaveis = sum(1 for r in resultados if r.condicao == rotulos.CONDICAO_SAUDAVEL)
indeterminados = sum(1 for r in resultados if r.indeterminado)

coluna_a, coluna_b, coluna_c, coluna_d = st.columns(4)
coluna_a.metric("Imagens", len(por_arquivo))
coluna_b.metric("Saudáveis", saudaveis)
coluna_c.metric("Doentes", doentes)
coluna_d.metric("Indeterminados", indeterminados)

if doentes:
    st.warning(f"{doentes} laudo(s) apontam sintomas. Veja as recomendações de manejo abaixo.")

st.divider()


# --------------------------------------------------------------------------- #
# Laudos
# --------------------------------------------------------------------------- #
def render_laudo(resultado) -> None:
    st.markdown(
        f"{badge(resultado.condicao, resultado.condicao)} "
        f"<span style='font-size:0.75rem;color:#6b7280;'>{html.escape(resultado.motor)}</span>",
        unsafe_allow_html=True,
    )
    st.markdown(f"**{resultado.diagnostico}**")

    detalhes = [parte for parte in (resultado.especie, resultado.agente) if parte and parte != "—"]
    if detalhes:
        st.caption(" · ".join(detalhes))

    st.caption(f"Confiança: {resultado.confianca_texto}")
    if resultado.confianca is not None:
        st.progress(min(resultado.confianca, 1.0))

    st.markdown(f"**Sintomas.** {resultado.sintomas}")
    st.markdown(f"**Manejo.** {resultado.manejo}")

    if resultado.observacoes:
        st.caption(resultado.observacoes)

    if resultado.ranking:
        with st.expander("Ranking do classificador"):
            for posicao, predicao in enumerate(resultado.ranking, start=1):
                st.markdown(f"{posicao}. {predicao.rotulo} — `{predicao.confianca:.2%}`")


imagens_por_nome = {arquivo.name: arquivo for arquivo in arquivos}
comparando = modo == diagnostico.MODO_COMPARACAO
por_linha = 2 if comparando else 3

nomes = list(por_arquivo)
for inicio in range(0, len(nomes), por_linha):
    colunas = st.columns(por_linha)

    for coluna, nome in zip(colunas, nomes[inicio : inicio + por_linha]):
        with coluna, st.container(border=True):
            if nome in imagens_por_nome:
                st.image(imagens_por_nome[nome], width="stretch")
            st.caption(html.escape(nome))

            laudos = por_arquivo[nome]
            if len(laudos) == 1:
                render_laudo(laudos[0])
            else:
                # Modo comparação: CNN à esquerda, generalista à direita.
                for painel, laudo in zip(st.columns(len(laudos)), sorted(laudos, key=lambda r: r.motor != MOTOR_CNN)):
                    with painel:
                        render_laudo(laudo)


# --------------------------------------------------------------------------- #
# Exportação
# --------------------------------------------------------------------------- #
st.divider()
st.subheader("Exportação dos resultados")
st.caption(
    "O CSV usa separador `;` e decimal `,` para abrir corretamente no Excel em "
    "português. A coluna `confianca` traz a probabilidade do CNN e fica vazia "
    "no motor generalista, que só autoavalia a confiança em palavras."
)

coluna_csv, coluna_json, coluna_salvar = st.columns(3)

coluna_csv.download_button(
    "⬇️ Baixar CSV",
    data=exportacao.gerar_csv(resultados),
    file_name="agrosmart_resultados.csv",
    mime="text/csv",
    width="stretch",
)

coluna_json.download_button(
    "⬇️ Baixar JSON",
    data=exportacao.gerar_json(resultados),
    file_name="agrosmart_resultados.json",
    mime="application/json",
    width="stretch",
)

if coluna_salvar.button("💾 Salvar em `exportacoes/`", width="stretch"):
    caminho_csv, caminho_json = exportacao.salvar(resultados, PASTA_EXPORTACOES)
    st.success(f"Arquivos gravados: `{caminho_csv.name}` e `{caminho_json.name}`")

with st.expander("Pré-visualizar tabela exportada"):
    st.dataframe(exportacao.para_linhas(resultados), width="stretch", hide_index=True)
