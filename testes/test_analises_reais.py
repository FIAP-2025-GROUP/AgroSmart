"""Análises reais: gravação pela interface, compatibilidade e painel ao vivo.

O diagnóstico é substituído por um dublê — nada de TensorFlow, modelo CNN ou
Gemini. O banco é o temporário do `conftest`.
"""

from __future__ import annotations

import hashlib
import io
import sqlite3

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from src import analitica_operacional, diagnostico, historico
from src.pipeline import ingestao
from src.tipos import MOTOR_CNN, MOTOR_VLM, Resultado


def _resultado(motor=MOTOR_CNN, condicao="doente", diagnostico_="Requeima", data="2026-09-21T10:00:00"):
    cnn = motor == MOTOR_CNN
    return Resultado(
        arquivo="folha.png", data_hora=data, motor=motor, especie="Tomate" if cnn else "Tomateiro",
        diagnostico=diagnostico_, condicao=condicao, confianca=0.91 if cnn else None,
        confianca_texto="91.0%" if cnn else "alta (autoavaliada)", agente="Oomiceto",
        sintomas="s", manejo="m", modelo_versao="v",
    )


def _png(cor=(10, 120, 30)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), cor).save(buffer, format="PNG")
    return buffer.getvalue()


def _contar(tabela: str) -> int:
    if not historico.CAMINHO_BANCO.exists():
        return 0
    with sqlite3.connect(historico.CAMINHO_BANCO) as conexao:
        return conexao.execute(f"SELECT COUNT(*) FROM {tabela}").fetchone()[0]


@pytest.fixture
def cliente():
    import servidor

    return TestClient(servidor.app)


@pytest.fixture
def dublê(monkeypatch):
    """Substitui o diagnóstico: devolve CNN+VLM no modo comparação, um laudo nos demais."""
    chamadas = {"avisos": []}

    def analisar_lote(itens, modo, limiar, **_):
        chamadas["limiar"] = limiar
        if modo == diagnostico.MODO_COMPARACAO:
            return [_resultado(MOTOR_CNN), _resultado(MOTOR_VLM, diagnostico_="Míldio tardio")], []
        motor = MOTOR_VLM if modo == diagnostico.MODO_VLM else MOTOR_CNN
        return [_resultado(motor)], list(chamadas["avisos"])

    monkeypatch.setattr(diagnostico, "analisar_lote", analisar_lote)
    return chamadas


def _enviar(cliente, conteudo=None, **campos):
    dados = {"modo": diagnostico.MODO_COMPARACAO, "limiar": "0.6", **campos}
    return cliente.post("/api/analisar", files={"imagem": ("folha.png", conteudo or _png(), "image/png")},
                        data=dados)


# --------------------------------------------------------------------------- envio pela interface
def test_comparacao_vira_uma_analise_uma_imagem_dois_laudos(cliente, dublê):
    conteudo = _png()
    resposta = _enviar(cliente, conteudo, uf="sp", municipio="  Campinas ", propriedade="Sítio X", talhao="")
    assert resposta.status_code == 200, resposta.text
    corpo = resposta.json()
    assert corpo["id_analise"] and len(corpo["laudos"]) == 2
    assert all(laudo["id_historico"] for laudo in corpo["laudos"])

    with sqlite3.connect(historico.CAMINHO_BANCO) as conexao:
        analise = conexao.execute("SELECT * FROM analises").fetchone()
        vinculos = conexao.execute("SELECT id_analise, id_laudo FROM analise_laudos ORDER BY id_laudo").fetchall()
    id_analise, id_imagem, arquivo, _, modo, limiar, uf, municipio, propriedade, talhao = analise
    assert id_analise == corpo["id_analise"]
    assert id_imagem == hashlib.sha256(conteudo).hexdigest()
    assert (arquivo, modo, limiar) == ("folha.png", diagnostico.MODO_COMPARACAO, 0.6)
    assert (uf, municipio, propriedade, talhao) == ("SP", "Campinas", "Sítio X", None)
    assert [v[1] for v in vinculos] == [laudo["id_historico"] for laudo in corpo["laudos"]]

    # E entra no painel na hora, sem Spark.
    painel = cliente.get("/api/analitica", params={"origem": "real"}).json()
    cards = painel["cards"]
    assert (cards["analises"], cards["laudos"], cards["imagens_distintas"]) == (1, 2, 1)
    assert cards["comparacoes"] == 1 and cards["discordancias"] == 0
    assert painel["graficos"]["localidades"][0]["municipio"] == "Campinas"
    assert painel["graficos"]["diagnosticos"][0]["chave"] == "Requeima"


def test_limiar_efetivo_nulo_quando_o_cnn_nao_roda(cliente, dublê):
    _enviar(cliente, modo=diagnostico.MODO_VLM)
    dublê["avisos"] = ["Motor CNN indisponível, seguindo só com o generalista. x"]
    _enviar(cliente, _png((1, 2, 3)), modo=diagnostico.MODO_AUTOMATICO)
    dublê["avisos"] = []
    _enviar(cliente, _png((4, 5, 6)), modo=diagnostico.MODO_CNN, limiar="0.7")
    with sqlite3.connect(historico.CAMINHO_BANCO) as conexao:
        limiares = [r[0] for r in conexao.execute("SELECT limiar FROM analises ORDER BY rowid")]
    assert limiares == [None, None, 0.7]


@pytest.mark.parametrize("campos", [{"uf": "XX"}, {"municipio": "a" * 81}])
def test_localidade_invalida_e_recusada_sem_gravar(cliente, dublê, campos):
    resposta = _enviar(cliente, **campos)
    assert resposta.status_code == 400
    assert _contar("laudos") == 0 and _contar("analises") == 0


def test_sem_salvar_nada_e_gravado(cliente, dublê):
    corpo = _enviar(cliente, salvar="false").json()
    assert corpo["id_analise"] is None
    assert all(laudo["id_historico"] is None for laudo in corpo["laudos"])
    assert _contar("laudos") == 0 and _contar("analises") == 0


def test_mesma_imagem_de_novo_e_reanalise_nao_imagem_nova(cliente, dublê):
    conteudo = _png()
    _enviar(cliente, conteudo, modo=diagnostico.MODO_CNN)
    _enviar(cliente, conteudo, modo=diagnostico.MODO_CNN)
    cards = cliente.get("/api/analitica", params={"origem": "real"}).json()["cards"]
    assert (cards["analises"], cards["imagens_distintas"], cards["reanalises"]) == (2, 1, 1)


def test_endpoint_sem_campos_novos_continua_funcionando(cliente, dublê):
    resposta = cliente.post("/api/analisar", files={"imagem": ("a.png", _png(), "image/png")})
    assert resposta.status_code == 200
    assert resposta.json()["id_analise"]


# --------------------------------------------------------------------------- histórico
def test_gravacao_e_transacional(monkeypatch):
    historico.salvar_analise([_resultado()], Image.new("RGB", (4, 4)), b"a", "a.png", "m", 0.6)
    existente = historico.listar()[0]["id"]
    with sqlite3.connect(historico.CAMINHO_BANCO) as conexao:
        id_existente = conexao.execute("SELECT id_analise FROM analises").fetchone()[0]

    import uuid

    monkeypatch.setattr(uuid, "uuid4", lambda: id_existente)  # força conflito de chave
    with pytest.raises(sqlite3.IntegrityError):
        historico.salvar_analise([_resultado(), _resultado(MOTOR_VLM)], Image.new("RGB", (4, 4)),
                                 b"b", "b.png", "m", 0.6)
    assert _contar("laudos") == 1 and _contar("analises") == 1 and _contar("analise_laudos") == 1
    assert historico.listar()[0]["id"] == existente


def test_remover_e_limpar_em_cascata():
    _, ids = historico.salvar_analise([_resultado(), _resultado(MOTOR_VLM)], Image.new("RGB", (4, 4)),
                                      b"x", "x.png", diagnostico.MODO_COMPARACAO, 0.6)
    historico.remover(ids[0])
    assert (_contar("laudos"), _contar("analise_laudos"), _contar("analises")) == (1, 1, 1)
    historico.remover(ids[1])
    assert (_contar("laudos"), _contar("analise_laudos"), _contar("analises")) == (0, 0, 0)

    historico.salvar_analise([_resultado()], Image.new("RGB", (4, 4)), b"y", "y.png", "m", 0.6)
    historico.salvar(_resultado(), Image.new("RGB", (4, 4)))
    assert historico.limpar() == 2
    assert (_contar("laudos"), _contar("analise_laudos"), _contar("analises")) == (0, 0, 0)


def test_banco_antigo_ganha_tabelas_sem_perder_laudos():
    historico.CAMINHO_BANCO.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(historico.CAMINHO_BANCO) as conexao:  # esquema da Fase 1, sem as tabelas novas
        conexao.executescript(historico.ESQUEMA.split("CREATE TABLE IF NOT EXISTS analises")[0])
        conexao.execute("INSERT INTO laudos (arquivo, data_hora, motor, condicao) "
                        "VALUES ('velho.jpg', '2026-01-01T10:00:00', 'CNN especializado', 'saudável')")
    # Extração de um banco que ainda não foi aberto pelo app novo.
    registros = ingestao.ler_historico_sqlite(historico.CAMINHO_BANCO)
    assert len(registros) == 1 and registros[0]["id_analise"] is None

    assert historico.total() == 1  # abrir pelo app cria as tabelas novas
    assert _contar("analises") == 0
    assert historico.listar()[0]["arquivo"] == "velho.jpg"
    assert historico.como_resultados()[0].arquivo == "velho.jpg"


def test_extracao_traz_identidade_so_para_laudos_vinculados():
    historico.salvar(_resultado(condicao="saudável", diagnostico_="Saudável"), Image.new("RGB", (4, 4)))
    id_analise, _ = historico.salvar_analise(
        [_resultado(), _resultado(MOTOR_VLM)], Image.new("RGB", (4, 4)), b"img", "a.png",
        diagnostico.MODO_COMPARACAO, 0.6, {"uf": "PR", "municipio": "Cascavel"})
    registros = ingestao.ler_historico_sqlite(historico.CAMINHO_BANCO)
    assert [r["id_analise"] for r in registros] == [None, id_analise, id_analise]
    vinculado = registros[1]
    assert vinculado["id_imagem"] == hashlib.sha256(b"img").hexdigest()
    assert (vinculado["modo_solicitado"], vinculado["limiar_cnn"]) == (diagnostico.MODO_COMPARACAO, 0.6)
    assert (vinculado["uf"], vinculado["municipio"], vinculado["propriedade"]) == ("PR", "Cascavel", None)
    assert registros[0]["uf"] is None and registros[0]["modo_solicitado"] is None


def test_fonte_operacional_atualiza_quando_o_banco_muda():
    assert analitica_operacional.carregar().analises == []
    historico.salvar_analise([_resultado()], Image.new("RGB", (4, 4)), b"1", "a.png", "m", 0.6)
    assert len(analitica_operacional.carregar().analises) == 1
    historico.salvar_analise([_resultado()], Image.new("RGB", (4, 4)), b"2", "b.png", "m", 0.6)
    assert len(analitica_operacional.carregar().analises) == 2


def test_laudo_real_invalido_e_contado_como_rejeitado():
    historico.salvar_analise([_resultado(condicao="talvez")], Image.new("RGB", (4, 4)), b"1", "a.png", "m", 0.6)
    historico.salvar_analise([_resultado()], Image.new("RGB", (4, 4)), b"2", "b.png", "m", 0.6)
    vivo = analitica_operacional.carregar()
    assert len(vivo.laudos) == 1
    metricas = {m["metrica"]: m["valor"] for m in vivo.metricas}
    assert metricas["registros_recebidos"] == 2 and metricas["rejeitados"] == 1
    assert metricas["rejeitado:condicao_invalida"] == 1
