"""API analítica e publicação de pacotes — sem Spark, com pacotes montados à mão."""

from __future__ import annotations

import json
import subprocess
import sys
from collections import Counter

import pytest
from fastapi.testclient import TestClient

from pathlib import Path

from conftest import RAIZ
from src.pipeline import contratos as c
from src.pipeline import pacote


# --------------------------------------------------------------------------- construção
def analise(id_analise, origem="simulado", condicao="saudavel", dia="2026-08-03", uf="SP",
            municipio="Campinas", cultura="Tomate", motor="cnn", id_imagem="auto",
            categoria=None, diagnostico=None, reanalise=False, comparacao=False, concordancia=None):
    categoria = categoria or {"saudavel": "saudavel", "indeterminado": "indeterminado"}.get(condicao, "doenca")
    return {
        "id_analise": id_analise, "origem_dado": origem,
        "id_imagem": f"img-{id_analise}" if id_imagem == "auto" else id_imagem,
        "data_analise_ts": f"{dia}T10:00:00-03:00", "data_dia": dia, "semana": dia,
        "uf": uf, "municipio": municipio, "propriedade": "P1" if municipio else None,
        "talhao": "T1" if municipio else None, "cultura": cultura, "condicao": condicao,
        "diagnostico_normalizado": diagnostico or {"saudavel": "Saudável", "indeterminado": "Indeterminado"}.get(condicao, "Requeima"),
        "categoria_problema": categoria, "motor_principal": motor, "comparacao": comparacao,
        "concordancia_motores": concordancia, "e_reanalise": reanalise if id_imagem else None,
    }


def laudos_de(a, motores=None):
    saida = []
    for i, motor in enumerate(motores or [a["motor_principal"]]):
        saida.append({
            "id_laudo": f"{a['id_analise']}-l{i}", "id_analise": a["id_analise"], "origem_dado": a["origem_dado"],
            "data_analise_ts": a["data_analise_ts"], "data_dia": a["data_dia"], "uf": a["uf"],
            "municipio": a["municipio"], "propriedade": a["propriedade"], "talhao": a["talhao"],
            "cultura": a["cultura"], "condicao": a["condicao"],
            "diagnostico_normalizado": a["diagnostico_normalizado"], "diagnostico_original": a["diagnostico_normalizado"],
            "categoria_problema": a["categoria_problema"], "motor": motor,
            "confianca": 0.9 if motor == "cnn" else None, "confianca_texto": "90.0%" if motor == "cnn" else "alta",
        })
    return saida


def legado(numero, condicao="saudavel", dia="2026-09-20"):
    return {"id_laudo": f"agrosmart-local:laudo:{numero}", "id_analise": None, "origem_dado": "real",
            "data_analise_ts": f"{dia}T10:00:00-03:00", "data_dia": dia, "uf": None, "municipio": None,
            "propriedade": None, "talhao": None, "cultura": "Maçã", "condicao": condicao,
            "diagnostico_normalizado": "Saudável", "diagnostico_original": "Saudável",
            "categoria_problema": {"doente": "doenca"}.get(condicao, condicao), "motor": "cnn",
            "confianca": 0.95, "confianca_texto": "95.0%"}


def publicar(analises, laudos, id_execucao="exec_a", marcar=True, destino=None):
    destino = destino or c.PASTA_PROCESSADOS
    grupos = Counter((a["semana"], a["origem_dado"], a["uf"], a["municipio"], a["cultura"]) for a in analises)
    agregados = [json.dumps({"semana": k[0], "origem_dado": k[1], "n_analises": v}) for k, v in grupos.items()]
    qualidade = {"metricas": [{"origem_dado": "simulado", "metrica": "rejeitados", "valor": 1,
                               "denominador": 10, "unidade": "registro_raw"}],
                 "amostra_rejeitados": [{"id_laudo": "x", "origem_dado": "simulado", "motivo_rejeicao": "duplicado"}]}
    return pacote.publicar(
        id_execucao,
        analises=[json.dumps(a, ensure_ascii=False) for a in analises],
        laudos=[json.dumps(x, ensure_ascii=False) for x in laudos],
        agregados=agregados, qualidade=qualidade,
        metadados={"processado_em": "2026-09-30T12:00:00Z", "ambiente": "teste"},
        destino=destino, marcar_como_atual=marcar,
    )


@pytest.fixture
def cliente():
    import servidor

    return TestClient(servidor.app)


@pytest.fixture
def basico():
    """6 análises simuladas + 1 real identificada + 2 laudos legados."""
    analises = [
        analise("s1", condicao="doente", comparacao=True, concordancia=True),
        analise("s2", condicao="saudavel", id_imagem="img-s1", reanalise=True, dia="2026-08-10"),
        analise("s3", condicao="saudavel", uf="PR", municipio="Cascavel", cultura="Milho", dia="2026-08-10"),
        analise("s4", condicao="doente", uf="PR", municipio="Cascavel", cultura="Milho",
                categoria="praga", diagnostico="Ácaro-rajado", motor="vlm", dia="2026-08-17"),
        analise("s5", condicao="indeterminado", uf=None, municipio=None, cultura=None, id_imagem=None,
                motor="vlm", dia="2026-08-17"),
        analise("s6", condicao="saudavel", dia="2026-08-17"),
        analise("r1", origem="real", condicao="doente", uf="MG", municipio="Uberlândia", cultura="Milho",
                dia="2026-09-21"),
    ]
    laudos = laudos_de(analises[0], ["cnn", "vlm"])
    for a in analises[1:]:
        laudos += laudos_de(a)
    laudos += [legado(1), legado(2, condicao="doente")]
    publicar(analises, laudos)
    return analises, laudos


def consultar(cliente, **params):
    resposta = cliente.get("/api/analitica", params=params)
    assert resposta.status_code == 200, resposta.text
    return resposta.json()


# --------------------------------------------------------------------------- disponibilidade
def test_sem_pacote_responde_indisponivel(cliente):
    dados = consultar(cliente)
    assert dados["disponivel"] is False
    assert "executar_pipeline" in dados["mensagem"]


def test_dashboard_e_link_na_interface_atual(cliente):
    resposta = cliente.get("/dashboard")
    assert resposta.status_code == 200 and "Painel" in resposta.text
    assert 'href="/dashboard"' in cliente.get("/").text


def test_servidor_nao_carrega_spark_nem_tensorflow():
    codigo = (
        "import sys; sys.path.insert(0, r'%s');"
        "import servidor; from fastapi.testclient import TestClient;"
        "TestClient(servidor.app).get('/api/analitica');"
        "assert 'pyspark' not in sys.modules, 'pyspark carregado';"
        "assert 'tensorflow' not in sys.modules, 'tensorflow carregado'" % RAIZ
    )
    resultado = subprocess.run([sys.executable, "-c", codigo], capture_output=True, text=True, cwd=RAIZ)
    assert resultado.returncode == 0, resultado.stderr


# --------------------------------------------------------------------------- indicadores
def test_cards_com_numerador_e_denominador(cliente, basico):
    dados = consultar(cliente, origem="simulado")
    cards = dados["cards"]
    assert cards["analises"] == 6
    assert cards["laudos"] == 7                      # s1 tem dois laudos (comparação)
    assert cards["saudavel"] == {"num": 3, "den": 6, "pct": 0.5}
    assert cards["doente"] == {"num": 2, "den": 6, "pct": 0.3333}
    assert cards["indeterminado"] == {"num": 1, "den": 6, "pct": 0.1667}
    # s2 reanalisa a imagem de s1; s5 não tem id_imagem: 4 imagens distintas, não 6.
    assert cards["imagens_distintas"] == 4
    assert cards["analises_sem_id_imagem"] == 1
    assert cards["reanalises"] == 1
    assert cards["comparacoes"] == 1 and cards["discordancias"] == 0


def test_graficos_por_analise_e_por_laudo(cliente, basico):
    g = consultar(cliente, origem="simulado")["graficos"]
    # Laudos: s1 (comparação) emite CNN e VLM; s4 e s5 só VLM; o resto só CNN.
    assert {x["chave"]: x["n"] for x in g["motores_laudos"]} == {"cnn": 4, "vlm": 3}
    assert {x["chave"]: x["n"] for x in g["motores_analises"]} == {"cnn": 4, "vlm": 2}
    assert {x["chave"]: x["n"] for x in g["categorias"]} == {
        "saudavel": 3, "doenca": 1, "praga": 1, "indeterminado": 1}
    # Diagnósticos: só entre as análises doentes (denominador 2).
    assert sorted((d["chave"], d["n"], d["den"]) for d in g["diagnosticos"]) == [
        ("Requeima", 1, 2), ("Ácaro-rajado", 1, 2)]

    evolucao = {s["semana"]: s for s in g["evolucao"]}
    assert evolucao["2026-08-17"]["n_analises"] == 3
    assert evolucao["2026-08-17"]["n_doente"] == 1
    assert evolucao["2026-08-17"]["pct_doente"] == 0.3333

    locais = {(x["uf"], x["municipio"]): x for x in g["localidades"]}
    assert locais[("PR", "Cascavel")]["doente"] == {"num": 1, "den": 2, "pct": 0.5}
    assert locais[(None, None)]["n_analises"] == 1  # localidade incompleta, numa linha só


def test_evolucao_nao_tira_media_de_percentuais(cliente):
    # Semana A: 1 de 1 doente (100%); semana B: 1 de 9 (11%). Total: 2/10 = 20%, não 55%.
    analises = [analise("a1", condicao="doente", dia="2026-08-03")]
    analises += [analise(f"b{i}", condicao="doente" if i == 0 else "saudavel", dia="2026-08-10") for i in range(9)]
    publicar(analises, [x for a in analises for x in laudos_de(a)])
    dados = consultar(cliente, origem="simulado")
    assert dados["cards"]["doente"]["pct"] == 0.2
    assert [s["pct_doente"] for s in dados["graficos"]["evolucao"]] == [1.0, 0.1111]


# --------------------------------------------------------------------------- origens
def _real(condicao="doente", motor=None, data="2026-09-21T09:00:00", diagnostico=None):
    """Resultado da Fase 1, como o diagnóstico produziria."""
    from src.tipos import MOTOR_CNN, Resultado

    return Resultado(
        arquivo="campo.jpg", data_hora=data, motor=motor or MOTOR_CNN, especie="Milho",
        diagnostico=diagnostico or {"doente": "Ferrugem comum", "saudável": "Saudável"}.get(condicao, "Indeterminado"),
        condicao=condicao, confianca=0.83 if (motor or MOTOR_CNN) == MOTOR_CNN else None,
        confianca_texto="83.0%", agente="Fungo (Puccinia sorghi)", sintomas="s", manejo="m",
        modelo_versao="MobileNetV2 v1.0.0",
    )


def _analise_real(n=1, contexto=None, conteudo=b"foto-1", **kw):
    from PIL import Image

    from src import historico

    ids = []
    for i in range(n):
        ids.append(historico.salvar_analise(
            [_real(**kw)], Image.new("RGB", (4, 4)), conteudo + str(i).encode(), "campo.jpg",
            "Somente CNN especializado", 0.6, contexto or {"uf": "MG", "municipio": "Uberlândia"})[0])
    return ids


def test_reais_vem_do_sqlite_e_reais_do_pacote_sao_ignorados(cliente, basico):
    # O pacote `basico` traz 1 análise real e 2 laudos legados reais: ignorados.
    dados = consultar(cliente, origem="real")
    assert dados["origens"]["real"] == {"analises": 0, "laudos": 0}
    assert "ignorados" in dados["pacote"]["aviso"]
    _analise_real()
    dados = consultar(cliente, origem="real")
    assert dados["origens"]["real"] == {"analises": 1, "laudos": 1}
    assert dados["cards"]["doente"] == {"num": 1, "den": 1, "pct": 1.0}
    assert dados["fonte_real"]["analises"] == 1


def test_origem_padrao_simulados_ate_20_analises_reais(cliente, basico):
    _analise_real(19)
    dados = consultar(cliente)
    assert dados["origem_padrao"] == "simulado" and dados["origem"] == "simulado"
    assert dados["origens"]["real"]["analises"] == 19
    _analise_real(1, conteudo=b"foto-20-")
    dados = consultar(cliente)
    assert dados["origem_padrao"] == "real" and dados["cards"]["analises"] == 20


def test_origem_padrao_nao_forca_fonte_real_vazia(cliente):
    analises = [analise("s1")]
    publicar(analises, laudos_de(analises[0]))
    dados = consultar(cliente)
    assert dados["origem_padrao"] == "simulado"
    assert dados["origens"]["real"] == {"analises": 0, "laudos": 0}


def test_so_dados_reais_sem_pacote_ainda_disponivel(cliente):
    _analise_real()
    dados = consultar(cliente)
    assert dados["disponivel"] is True
    assert dados["origem"] == "real"  # sem simulados, a única origem com dados
    assert dados["pacote"]["id_execucao"] is None
    assert dados["cards"]["analises"] == 1


def test_real_so_com_laudos_sem_vinculo_mostra_sem_dados_e_nao_inventa_analises(cliente):
    from PIL import Image

    from src import historico

    publicar([analise("s1")], laudos_de(analise("s1")))
    for _ in range(3):  # laudos gravados sem análise (histórico antigo ou app Streamlit)
        historico.salvar(_real(condicao="saudável"), Image.new("RGB", (4, 4)))
    dados = consultar(cliente, origem="real")
    cards = dados["cards"]
    assert cards["analises"] == 0 and cards["laudos"] == 3 and cards["laudos_sem_analise"] == 3
    assert cards["saudavel"]["pct"] is None          # "sem dados", não 0%
    assert cards["imagens_distintas"] is None
    assert dados["graficos"]["motores_laudos"] == [{"chave": "cnn", "n": 3}]


def test_todas_soma_origens_e_simulado_nao_vaza_no_real(cliente, basico):
    _analise_real(2)
    todas = consultar(cliente, origem="todas")["cards"]
    real = consultar(cliente, origem="real")["cards"]
    simulado = consultar(cliente, origem="simulado")["cards"]
    assert (real["analises"], simulado["analises"]) == (2, 6)
    assert todas["analises"] == real["analises"] + simulado["analises"]
    assert todas["laudos"] == real["laudos"] + simulado["laudos"]
    linhas = consultar(cliente, origem="real")["tabela"]["itens"]
    assert {x["origem_dado"] for x in linhas} == {"real"}


# --------------------------------------------------------------------------- filtros
@pytest.mark.parametrize(("params", "analises_esperadas", "laudos_esperados"), [
    ({"uf": "PR"}, 2, 2),
    ({"municipio": "Campinas"}, 3, 4),
    ({"cultura": "Milho"}, 2, 2),
    ({"condicao": "doente"}, 2, 3),
    ({"categoria": "praga"}, 1, 1),
    ({"motor": "vlm"}, 2, 3),                 # análises pelo motor principal; laudos pelo próprio motor
    ({"inicio": "2026-08-10", "fim": "2026-08-10"}, 2, 2),
    ({"uf": "__nao_informado__"}, 1, 1),
    ({"cultura": "Café"}, 0, 0),
])
def test_filtros(cliente, basico, params, analises_esperadas, laudos_esperados):
    dados = consultar(cliente, origem="simulado", **params)
    assert dados["cards"]["analises"] == analises_esperadas
    assert dados["cards"]["laudos"] == laudos_esperados
    if analises_esperadas == 0:
        assert dados["cards"]["doente"]["pct"] is None


def test_opcoes_de_localidade_encadeadas(cliente, basico):
    opcoes = consultar(cliente, origem="simulado", uf="PR")["opcoes"]
    assert opcoes["municipio"] == ["Cascavel"]
    assert "__nao_informado__" in consultar(cliente, origem="simulado")["opcoes"]["uf"]


def test_parametros_invalidos(cliente, basico):
    assert cliente.get("/api/analitica", params={"origem": "outra"}).status_code == 400
    assert cliente.get("/api/analitica", params={"inicio": "03/08/2026"}).status_code == 400


def test_tabela_paginada_do_mais_recente(cliente, basico):
    tabela = consultar(cliente, origem="todas")["tabela"]
    assert tabela["total"] == 7 and tabela["paginas"] == 1  # só os laudos simulados do pacote
    datas = [x["data_analise_ts"] for x in tabela["itens"]]
    assert datas == sorted(datas, reverse=True)


# --------------------------------------------------------------------------- publicação
def test_publicacao_e_atomica_e_nunca_mistura_execucoes(cliente):
    publicar([analise("v1")], laudos_de(analise("v1")), id_execucao="exec_1")
    publicar([analise("n1"), analise("n2")], laudos_de(analise("n1")) + laudos_de(analise("n2")),
             id_execucao="exec_2")
    dados = consultar(cliente, origem="simulado")
    assert dados["pacote"]["id_execucao"] == "exec_2"
    assert dados["cards"]["analises"] == 2
    assert not list(c.PASTA_PROCESSADOS.glob(".tmp_*"))
    with pytest.raises(FileExistsError):
        publicar([analise("x")], laudos_de(analise("x")), id_execucao="exec_2")


def test_pacote_adulterado_ou_incompleto_nao_e_servido(cliente):
    publicar([analise("v1")], laudos_de(analise("v1")), id_execucao="exec_1")
    publicar([analise("n1"), analise("n2")], laudos_de(analise("n1")) + laudos_de(analise("n2")),
             id_execucao="exec_2")
    # Adultera o pacote atual: a API cai para a execução válida anterior e avisa.
    with open(c.PASTA_PROCESSADOS / "exec_2" / "analises.jsonl", "a", encoding="utf-8") as arquivo:
        arquivo.write(json.dumps(analise("intrusa")) + "\n")
    dados = consultar(cliente, origem="simulado")
    assert dados["pacote"]["id_execucao"] == "exec_1"
    assert dados["cards"]["analises"] == 1
    assert "ignorado" in dados["pacote"]["aviso"]

    # Uma pasta sem manifesto (publicação interrompida) nunca é escolhida.
    (c.PASTA_PROCESSADOS / "exec_9").mkdir()
    (c.PASTA_PROCESSADOS / "exec_9" / "analises.jsonl").write_text("", encoding="utf-8")
    assert consultar(cliente, origem="simulado")["pacote"]["id_execucao"] == "exec_1"


def test_manifesto_status_e_schema(tmp_path):
    pasta = publicar([analise("v1")], laudos_de(analise("v1")), marcar=False, destino=tmp_path)
    manifesto = json.loads((pasta / "manifesto.json").read_text(encoding="utf-8"))
    manifesto["status"] = "em_andamento"
    (pasta / "manifesto.json").write_text(json.dumps(manifesto), encoding="utf-8")
    with pytest.raises(pacote.PacoteInvalidoError, match="status"):
        pacote.validar(pasta)


def test_importar_processados_zip(tmp_path):
    import shutil

    import importar_processados

    origem = tmp_path / "origem"
    pasta = publicar([analise("z1")], laudos_de(analise("z1")), id_execucao="exec_zip",
                     marcar=False, destino=origem)
    arquivo_zip = shutil.make_archive(str(tmp_path / "exec_zip"), "zip", root_dir=origem, base_dir=pasta.name)

    final = importar_processados.importar(tmp_path / "exec_zip.zip", destino=c.PASTA_PROCESSADOS)
    assert final.name == "exec_zip"
    assert json.loads((c.PASTA_PROCESSADOS / "ATUAL.json").read_text(encoding="utf-8"))["id_execucao"] == "exec_zip"
    assert arquivo_zip.endswith(".zip")


# =========================================================================== #
# Rodada de robustez: validação de pacote (C1)
# =========================================================================== #
def _manifesto(pasta):
    return json.loads((pasta / "manifesto.json").read_text(encoding="utf-8"))


def _gravar_manifesto(pasta, manifesto):
    (pasta / "manifesto.json").write_text(json.dumps(manifesto), encoding="utf-8")


def _reescrever(pasta, nome, conteudo):
    """Troca um arquivo E atualiza o hash no manifesto: só a checagem de
    estrutura/contagem pode pegar o defeito."""
    (pasta / nome).write_text(conteudo, encoding="utf-8", newline="\n")
    manifesto = _manifesto(pasta)
    manifesto["arquivos"][nome]["sha256"] = pacote.sha256(pasta / nome)
    _gravar_manifesto(pasta, manifesto)


def _pacote_valido(id_execucao="exec_val"):
    a = analise("v1")
    return publicar([a], laudos_de(a), id_execucao=id_execucao, marcar=False)


def _com_arquivo(p, nome, **mudancas):
    manifesto = _manifesto(p)
    manifesto["arquivos"][nome] = {**manifesto["arquivos"][nome], **mudancas}
    return manifesto


DEFEITOS = {
    "manifesto_lista": lambda p: (p / "manifesto.json").write_text("[]", encoding="utf-8"),
    "manifesto_vazio": lambda p: (p / "manifesto.json").write_text("{}", encoding="utf-8"),
    "manifesto_ilegivel": lambda p: (p / "manifesto.json").write_text("{nao", encoding="utf-8"),
    "arquivos_lista": lambda p: _gravar_manifesto(p, {**_manifesto(p), "arquivos": []}),
    "arquivos_sem_hash": lambda p: _gravar_manifesto(p, _com_arquivo(p, "laudos.jsonl", sha256=None)),
    "status_errado": lambda p: _gravar_manifesto(p, {**_manifesto(p), "status": 1}),
    "schema_numero": lambda p: _gravar_manifesto(p, {**_manifesto(p), "versao_schema": 2}),
    "id_outra_pasta": lambda p: _gravar_manifesto(p, {**_manifesto(p), "id_execucao": "exec_outro"}),
    "arquivo_ausente": lambda p: (p / "laudos.jsonl").unlink(),
    "hash_incorreto": lambda p: (p / "laudos.jsonl").write_text("{}\n", encoding="utf-8"),
    "jsonl_invalido_hash_ok": lambda p: _reescrever(p, "analises.jsonl", "{nao e json\n"),
    "jsonl_nao_objeto_hash_ok": lambda p: _reescrever(p, "analises.jsonl", "[1, 2]\n"),
    "registro_sem_campos_hash_ok": lambda p: _reescrever(p, "laudos.jsonl", '{"id_laudo": "x"}\n'),
    "enum_invalido_hash_ok": lambda p: _reescrever(
        p, "analises.jsonl", json.dumps({**analise("v1"), "condicao": "talvez"}) + "\n"),
    "contagem_divergente": lambda p: _gravar_manifesto(p, _com_arquivo(p, "laudos.jsonl", linhas=99)),
    "por_origem_divergente": lambda p: _gravar_manifesto(
        p, {**_manifesto(p), "por_origem": {"simulado": {"analises": 5, "laudos": 1}}}),
    "qualidade_lista": lambda p: _reescrever(p, "qualidade.json", "[]"),
}


@pytest.mark.parametrize("defeito", list(DEFEITOS))
def test_pacote_malformado_vira_erro_controlado(defeito):
    pasta = _pacote_valido()
    DEFEITOS[defeito](pasta)
    with pytest.raises(pacote.PacoteInvalidoError):
        pacote.ler(pasta)


@pytest.mark.parametrize("defeito", ["manifesto_lista", "manifesto_vazio", "arquivos_lista",
                                     "jsonl_invalido_hash_ok", "contagem_divergente"])
def test_api_nunca_responde_500_por_pacote_malformado(cliente, defeito):
    pasta = _pacote_valido()
    pacote.marcar_atual("exec_val")
    DEFEITOS[defeito](pasta)
    resposta = cliente.get("/api/analitica")
    assert resposta.status_code == 200
    dados = resposta.json()
    assert dados["disponivel"] is False
    assert "ignorado" in (dados["aviso"] or "")


def test_fallback_so_para_pacote_anterior_valido(cliente):
    a = analise("antigo")
    publicar([a], laudos_de(a), id_execucao="exec_1")
    b, b2 = analise("novo"), analise("novo2")
    publicar([b, b2], laudos_de(b) + laudos_de(b2), id_execucao="exec_2")
    pasta_ruim = _pacote_valido("exec_3")  # mais recente, mas será corrompido
    DEFEITOS["manifesto_lista"](pasta_ruim)
    (c.PASTA_PROCESSADOS / "ATUAL.json").write_text(json.dumps({"id_execucao": "exec_3"}), encoding="utf-8")
    dados = consultar(cliente, origem="simulado")
    assert dados["pacote"]["id_execucao"] == "exec_2"  # o válido mais recente, não o corrompido
    assert dados["cards"]["analises"] == 2


def test_pacote_valido_passa_e_publicar_nao_publica_o_que_nao_passaria():
    pasta = _pacote_valido()
    lido = pacote.ler(pasta)
    assert lido.manifesto["status"] == "completo" and len(lido.analises) == 1
    ruim = {**analise("x"), "condicao": "talvez"}
    with pytest.raises(pacote.PacoteInvalidoError):
        publicar([ruim], laudos_de(analise("x")), id_execucao="exec_ruim", marcar=False)
    assert not (c.PASTA_PROCESSADOS / "exec_ruim").exists()
    assert not list(c.PASTA_PROCESSADOS.glob(".tmp_*"))


def test_identidade_repetida_no_pacote_e_recusada():
    a = analise("dup")
    with pytest.raises(pacote.PacoteInvalidoError, match="repetido"):
        publicar([a, a], laudos_de(a), id_execucao="exec_dup", marcar=False)


# =========================================================================== #
# Filtros estritos (I1)
# =========================================================================== #
@pytest.mark.parametrize("params", [
    {"inicio": "20260803"},
    {"fim": "03/08/2026"},
    {"inicio": "2026-02-30"},
    {"inicio": "2026-8-3"},
    {"inicio": "2026-08-17", "fim": "2026-08-03"},   # intervalo invertido
    {"origem": "outra"},
    {"motor": "gpt"},
    {"condicao": "talvez"},
    {"categoria": "virus"},
    {"pagina": "0"},
])
def test_filtros_invalidos_sao_recusados(cliente, basico, params):
    resposta = cliente.get("/api/analitica", params=params)
    assert resposta.status_code == 400, resposta.text


def test_filtro_de_data_valido_e_canonico(cliente, basico):
    dados = consultar(cliente, origem="simulado", inicio="2026-08-10", fim="2026-08-10")
    assert dados["filtros"] == {"inicio": "2026-08-10", "fim": "2026-08-10"}
    assert dados["cards"]["analises"] == 2


def test_paginacao_com_mais_de_uma_pagina(cliente):
    analises = [analise(f"p{i:02d}", dia=f"2026-08-{(i % 28) + 1:02d}") for i in range(30)]
    publicar(analises, [x for a in analises for x in laudos_de(a)])
    p1 = consultar(cliente, origem="simulado")["tabela"]
    p2 = consultar(cliente, origem="simulado", pagina=2)["tabela"]
    p9 = consultar(cliente, origem="simulado", pagina=9)["tabela"]
    assert (p1["total"], p1["paginas"], len(p1["itens"])) == (30, 2, 25)
    assert (p2["pagina"], len(p2["itens"])) == (2, 5)
    assert p9["pagina"] == 2  # além do fim: última página
    ids = [x["id_laudo"] for x in p1["itens"] + p2["itens"]]
    assert len(ids) == len(set(ids)) == 30
    assert p1["itens"][-1]["data_analise_ts"] >= p2["itens"][0]["data_analise_ts"]


# =========================================================================== #
# Caminhos (I3)
# =========================================================================== #
@pytest.mark.parametrize("id_execucao", [
    "..", "../exec_x", "exec_a/../../x", "exec_a/b", "exec_a\\b", "/etc/passwd",
    "C:\\Windows", "exec_..", "exec_a ", "", None, 123, "outro_nome",
])
def test_id_execucao_nao_escapa_da_pasta(id_execucao):
    with pytest.raises(pacote.PacoteInvalidoError):
        pacote.pasta_execucao(c.PASTA_PROCESSADOS, id_execucao)


def test_ponteiro_com_caminho_malicioso_e_ignorado(cliente):
    a = analise("ok")
    publicar([a], laudos_de(a), id_execucao="exec_ok")
    (c.PASTA_PROCESSADOS / "ATUAL.json").write_text(json.dumps({"id_execucao": "../../segredo"}),
                                                    encoding="utf-8")
    dados = consultar(cliente, origem="simulado")
    assert dados["pacote"]["id_execucao"] == "exec_ok"
    assert "ignorado" in dados["pacote"]["aviso"]


def test_publicar_recusa_id_malicioso():
    a = analise("x")
    with pytest.raises(pacote.PacoteInvalidoError):
        publicar([a], laudos_de(a), id_execucao="../fora", marcar=False)
    assert not (c.PASTA_PROCESSADOS.parent / "fora").exists()


# =========================================================================== #
# Importação (I2)
# =========================================================================== #
def _zip_de(tmp_path, nome, analises):
    import shutil

    origem = tmp_path / f"origem_{nome}_{len(analises)}"
    pasta = publicar(analises, [x for a in analises for x in laudos_de(a)], id_execucao=nome,
                     marcar=False, destino=origem)
    return Path(shutil.make_archive(str(origem / nome), "zip", root_dir=origem, base_dir=pasta.name))


def test_importacao_idempotente(tmp_path):
    import importar_processados

    arquivo = _zip_de(tmp_path, "exec_imp", [analise("i1")])
    primeiro = importar_processados.importar(arquivo)
    segundo = importar_processados.importar(arquivo)
    assert primeiro == segundo
    assert pacote.validar(primeiro)["id_execucao"] == "exec_imp"


def test_importacao_com_mesmo_id_e_conteudo_diferente_e_colisao(tmp_path):
    import importar_processados

    a = analise("atual")
    publicar([a], laudos_de(a), id_execucao="exec_atual")  # pacote servido hoje
    original = _zip_de(tmp_path, "exec_col", [analise("c1")])
    importar_processados.importar(original, marcar_atual=False)
    antes = (c.PASTA_PROCESSADOS / "exec_col" / "manifesto.json").read_bytes()

    diferente = _zip_de(tmp_path, "exec_col", [analise("c1"), analise("c2")])
    with pytest.raises(pacote.ColisaoPacoteError):
        importar_processados.importar(diferente)
    assert (c.PASTA_PROCESSADOS / "exec_col" / "manifesto.json").read_bytes() == antes
    atual = json.loads((c.PASTA_PROCESSADOS / "ATUAL.json").read_text(encoding="utf-8"))
    assert atual["id_execucao"] == "exec_atual"  # o ponteiro não foi trocado


def test_importacao_recusa_zip_com_caminho_suspeito(tmp_path):
    import zipfile

    import importar_processados

    arquivo = tmp_path / "malicioso.zip"
    with zipfile.ZipFile(arquivo, "w") as z:
        z.writestr("../fora/manifesto.json", "{}")
    with pytest.raises(pacote.PacoteInvalidoError, match="suspeito"):
        importar_processados.importar(arquivo)
