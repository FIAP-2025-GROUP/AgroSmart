"""Bronze → Silver → Gold sobre a fixture `raw_basico`, com resultados conhecidos.

A fixture (testes/fixtures/raw_basico/laudos.jsonl) tem 15 linhas:

* 7 laudos simulados válidos em 5 análises:
  an-1 comparação (CNN e VLM dizem requeima com nomes diferentes), an-2 reanálise
  da imagem de an-1 (saudável), an-3 VLM com texto livre e sem localidade, an-4
  comparação com CNN indeterminado e VLM saudável, an-5 praga sem id_imagem;
* 1 duplicado exato de ld-01;
* 4 inválidas: VLM com confiança numérica, data impossível, linha que não é
  JSON e condição desconhecida;
* 3 laudos reais: 2 legados sem id_analise e 1 análise real identificada.

Pulado automaticamente se PySpark ou Java não estiverem disponíveis.
"""

from __future__ import annotations

import pytest

# Antes de qualquer import do Spark: sem PySpark o módulo inteiro é pulado.
pytest.importorskip("pyspark")

from conftest import RAW_BASICO  # noqa: E402

pytestmark = pytest.mark.filterwarnings("ignore")


@pytest.fixture(scope="module")
def camadas(spark):
    from src.pipeline import transformacoes_spark as ts

    resultado = ts.executar(spark, str(RAW_BASICO), "exec_teste", "2026-09-30T12:00:00Z")
    return {nome: df for nome, df in resultado.items()}


def _por(df, chave):
    return {linha[chave]: linha.asDict() for linha in df.collect()}


def test_schema_explicito_do_raw():
    from src.pipeline import contratos as c
    from src.pipeline import transformacoes_spark as ts

    schema = ts.schema_raw()
    assert schema.fieldNames()[:-1] == c.NOMES_CAMPOS_RAW
    assert schema["confianca"].dataType.typeName() == "double"
    assert schema["data_analise"].dataType.typeName() == "string"


def test_bronze_preserva_tudo_inclusive_linha_corrompida(camadas):
    bronze = camadas["bronze"]
    assert bronze.count() == 15
    assert bronze.filter("_registro_corrompido IS NOT NULL").count() == 1
    assert {r["id_execucao"] for r in bronze.select("id_execucao").collect()} == {"exec_teste"}


def test_silver_rejeita_com_motivo_sem_derrubar_o_lote(camadas):
    rejeitados = camadas["silver_rejeitados"].collect()
    motivos = sorted(m for r in rejeitados for m in r["motivos"])
    assert motivos == sorted([
        "duplicado",
        "confianca_numerica_em_laudo_vlm",
        "data_invalida",
        "json_invalido_ou_tipo_incompativel",
        "condicao_invalida",
    ])
    assert all(r["registro_original"] for r in rejeitados)  # o original fica para auditoria
    assert camadas["silver"].count() == 10


def test_silver_deduplica_por_id_laudo(camadas):
    ids = [r["id_laudo"] for r in camadas["silver"].select("id_laudo").collect()]
    assert len(ids) == len(set(ids))
    assert ids.count("ld-01") == 1


def test_silver_normaliza_preservando_original(camadas):
    laudos = _por(camadas["silver"], "id_laudo")

    assert laudos["ld-02"]["diagnostico_original"] == "Míldio tardio"
    assert laudos["ld-02"]["diagnostico_normalizado"] == "Requeima"
    assert laudos["ld-02"]["cultura"] == "Tomate"
    assert laudos["ld-02"]["motor"] == "vlm" and laudos["ld-02"]["motor_original"] == "Gemini (VLM)"

    assert laudos["ld-04"]["cultura"] == "Roseira"
    assert laudos["ld-04"]["categoria_problema"] == "praga"  # inferida pela palavra-chave
    assert laudos["ld-04"]["diagnostico_mapeado"] is False

    assert laudos["ld-07"]["categoria_problema"] == "praga"
    assert laudos["ld-03"]["condicao"] == "saudavel" and laudos["ld-03"]["categoria_problema"] == "saudavel"
    assert laudos["ld-05"]["cultura"] is None  # CNN indeterminado não sabe a cultura
    assert laudos["ld-05"]["categoria_problema"] == "indeterminado"
    assert laudos["agrosmart-local:laudo:2"]["diagnostico_normalizado"] == "Oídio"


def test_silver_confianca_numerica_so_no_cnn(camadas):
    for linha in camadas["silver"].collect():
        if linha["motor"] == "vlm":
            assert linha["confianca"] is None
        else:
            assert 0 <= linha["confianca"] <= 1


def test_silver_datas_com_e_sem_fuso(camadas):
    laudos = _por(camadas["silver"], "id_laudo")
    # Legado sem fuso: interpretado no fuso da sessão (America/Sao_Paulo).
    assert str(laudos["agrosmart-local:laudo:1"]["data_dia"]) == "2026-09-20"
    assert str(laudos["ld-01"]["semana"]) == "2026-08-03"  # segunda-feira


def test_gold_analises_comparacao_reanalise_e_identidade(camadas):
    analises = _por(camadas["gold_analises"], "id_analise")
    assert set(analises) == {"an-1", "an-2", "an-3", "an-4", "an-5", "real-an-1"}

    an1 = analises["an-1"]
    assert an1["n_laudos"] == 2 and an1["comparacao"] and an1["concordancia_motores"] is True
    assert an1["motor_principal"] == "cnn" and an1["diagnostico_normalizado"] == "Requeima"

    an4 = analises["an-4"]  # CNN indeterminado: vale o VLM
    assert an4["motor_principal"] == "vlm" and an4["condicao"] == "saudavel"
    assert an4["cultura"] == "Milho" and an4["concordancia_motores"] is None

    assert analises["an-2"]["e_reanalise"] is True     # mesma img-1, nova análise
    assert analises["an-1"]["e_reanalise"] is False
    assert analises["an-5"]["e_reanalise"] is None     # sem id_imagem: desconhecido, não falso


def test_gold_laudos_inclui_legado_sem_analise(camadas):
    laudos = camadas["gold_laudos"]
    assert laudos.count() == 10
    assert laudos.filter("id_analise IS NULL").count() == 2


def test_gold_agregados_preservam_numerador_e_denominador(camadas):
    linhas = [r.asDict() for r in camadas["gold_agregados"].collect()]
    assert sum(r["n_analises"] for r in linhas) == 6
    simulados = [r for r in linhas if r["origem_dado"] == "simulado"]
    assert sum(r["n_doente"] for r in simulados) == 3
    assert sum(r["n_analises"] for r in simulados) == 5
    for r in linhas:
        assert r["n_saudavel"] + r["n_doente"] + r["n_indeterminado"] == r["n_analises"]
        assert r["pct_doente"] == round(r["n_doente"] / r["n_analises"], 4)


def test_gold_qualidade(camadas):
    q = {(r["origem_dado"], r["metrica"]): r.asDict() for r in camadas["gold_qualidade"].collect()}
    assert q[("simulado", "registros_recebidos")]["valor"] == 11
    assert q[("desconhecida", "rejeitado:json_invalido_ou_tipo_incompativel")]["valor"] == 1
    assert q[("simulado", "rejeitados")]["valor"] == 4
    assert q[("real", "laudo_sem_analise")]["valor"] == 2
    assert q[("real", "laudo_sem_analise")]["denominador"] == 3
    assert q[("simulado", "localidade_ausente")]["valor"] == 1   # ld-04
    assert q[("simulado", "analise_sem_id_imagem")]["valor"] == 1
    assert q[("simulado", "diagnostico_nao_mapeado")]["valor"] == 1
    assert q[("simulado", "reanalises")]["valor"] == 1


def test_publicacao_completa_e_leitura_pela_api(camadas, tmp_path):
    """Spark → pacote → API: os números do dashboard batem com a fixture."""
    from src import analitica
    from src.pipeline import pacote
    from src.pipeline import transformacoes_spark as ts

    destino = tmp_path / "processados"
    pasta = ts.exportar_pacote(
        camadas["gold_analises"], camadas["gold_laudos"], camadas["gold_agregados"],
        camadas["gold_qualidade"], camadas["silver_rejeitados"],
        id_execucao="exec_teste", processado_em="2026-09-30T12:00:00Z",
        metadados={"ambiente": "teste"}, destino=destino,
    )
    manifesto = pacote.validar(pasta)
    assert manifesto["status"] == "completo"
    assert manifesto["por_origem"] == {"simulado": {"analises": 5, "laudos": 7},
                                       "real": {"analises": 1, "laudos": 3}}

    sim = analitica.consultar({"origem": "simulado"}, raiz=destino)["cards"]
    assert (sim["analises"], sim["laudos"]) == (5, 7)
    assert sim["saudavel"] == {"num": 2, "den": 5, "pct": 0.4}
    assert sim["doente"] == {"num": 3, "den": 5, "pct": 0.6}
    assert sim["imagens_distintas"] == 3 and sim["analises_sem_id_imagem"] == 1
    assert sim["reanalises"] == 1 and sim["comparacoes"] == 2

    # Os laudos reais do pacote são ignorados: a visão Reais vem do SQLite ao vivo
    # (vazio neste teste). Assim nada é contado duas vezes.
    real = analitica.consultar({"origem": "real"}, raiz=destino)
    assert real["origem_padrao"] == "simulado"
    assert (real["cards"]["analises"], real["cards"]["laudos"]) == (0, 0)
    assert "ignorados" in real["pacote"]["aviso"]


# =========================================================================== #
# Rodada de robustez
# =========================================================================== #
def _raw(tmp_path, registros, nome="laudos.jsonl"):
    import json

    caminho = tmp_path / nome
    caminho.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in registros),
                       encoding="utf-8")
    return str(caminho)


def _laudo(**campos):
    from src.pipeline import contratos as c

    r = c.registro_vazio()
    r.update(versao_schema="2.0.0", origem_instancia="inst-a", origem_dado="simulado",
             data_analise="2026-08-03T09:00:00-03:00", motor="CNN especializado",
             especie_original="Tomate", condicao="saudável", diagnostico_original="Saudável",
             confianca=0.9, confianca_texto="90.0%", id_snapshot="snap_t", extraido_em="2026-09-01T00:00:00Z")
    r.update(campos)
    return r


def _publicar_e_consultar(camadas, destino, id_execucao, origem="simulado"):
    from src import analitica
    from src.pipeline import transformacoes_spark as ts

    ts.exportar_pacote(
        camadas["gold_analises"], camadas["gold_laudos"], camadas["gold_agregados"],
        camadas["gold_qualidade"], camadas["silver_rejeitados"],
        id_execucao=id_execucao, processado_em="2026-09-30T12:00:00Z",
        metadados={"ambiente": "teste", "contagens": {n: df.count() for n, df in camadas.items()}},
        destino=destino,
    )
    analitica._cache.clear()
    return analitica.consultar({"origem": origem}, raiz=destino)


# --------------------------------------------------------------------------- C2: RAW vazio
def test_raw_vazio_gera_pacote_valido_e_painel_sem_dados(spark, tmp_path):
    from src.pipeline import pacote
    from src.pipeline import transformacoes_spark as ts

    destino = tmp_path / "processados"
    # Antes havia dados: a execução vazia NÃO pode deixar o painel mostrando os antigos.
    cheio = ts.executar(spark, str(RAW_BASICO), "exec_cheio", "2026-09-30T12:00:00Z")
    assert _publicar_e_consultar(cheio, destino, "exec_cheio")["cards"]["analises"] == 5

    vazio = _raw(tmp_path, [])
    camadas = ts.executar(spark, vazio, "exec_vazio", "2026-09-30T13:00:00Z")
    assert {n: df.count() for n, df in camadas.items()} == {
        "bronze": 0, "silver": 0, "silver_rejeitados": 0, "gold_analises": 0,
        "gold_laudos": 0, "gold_agregados": 0, "gold_qualidade": 0}

    resposta = _publicar_e_consultar(camadas, destino, "exec_vazio", origem="todas")
    manifesto = pacote.validar(destino / "exec_vazio")
    assert manifesto["arquivos"]["analises.jsonl"]["linhas"] == 0
    assert manifesto["por_origem"] == {}
    assert resposta["disponivel"] is True
    assert resposta["pacote"]["id_execucao"] == "exec_vazio"
    cards = resposta["cards"]
    assert (cards["analises"], cards["laudos"], cards["imagens_distintas"]) == (0, 0, None)
    assert cards["saudavel"]["pct"] is None and cards["doente"]["pct"] is None
    assert resposta["tabela"]["total"] == 0 and resposta["graficos"]["evolucao"] == []


# --------------------------------------------------------------------------- C3: identidade
def test_mesmo_id_laudo_em_origens_diferentes_nao_e_duplicata(spark, tmp_path):
    from src.pipeline import transformacoes_spark as ts

    registros = [
        _laudo(id_laudo="ld-1", id_analise="an-1", origem_instancia="inst-a"),
        _laudo(id_laudo="ld-1", id_analise="an-1", origem_instancia="inst-b"),          # outra instalação
        _laudo(id_laudo="ld-1", id_analise="an-1", origem_instancia="inst-a", origem_dado="real"),
    ]
    camadas = ts.executar(spark, _raw(tmp_path, registros), "exec_id", "2026-09-30T12:00:00Z")
    assert camadas["silver_rejeitados"].count() == 0
    assert camadas["silver"].count() == 3
    # E o mesmo id_analise em três origens são três análises, não uma com três laudos.
    analises = camadas["gold_analises"].collect()
    assert len(analises) == 3 and all(a["n_laudos"] == 1 for a in analises)


def test_mesma_identidade_com_conteudo_diferente_e_conflito(spark, tmp_path):
    from src.pipeline import transformacoes_spark as ts

    registros = [
        _laudo(id_laudo="ld-1", id_analise="an-1", condicao="saudável"),
        _laudo(id_laudo="ld-1", id_analise="an-1", condicao="doente", diagnostico_original="Requeima"),
        _laudo(id_laudo="ld-2", id_analise="an-2"),
        _laudo(id_laudo="ld-2", id_analise="an-2", extraido_em="2026-09-02T00:00:00Z"),  # cópia idêntica
    ]
    camadas = ts.executar(spark, _raw(tmp_path, registros), "exec_conf", "2026-09-30T12:00:00Z")
    rejeitados = sorted((r["id_laudo"], r["motivo_rejeicao"]) for r in camadas["silver_rejeitados"].collect())
    assert rejeitados == [("ld-1", "conflito_identidade_laudo"), ("ld-1", "conflito_identidade_laudo"),
                          ("ld-2", "duplicado")]
    validos = camadas["silver"].collect()
    assert [r["id_laudo"] for r in validos] == ["ld-2"]
    assert validos[0]["extraido_em"] == "2026-09-02T00:00:00Z"  # desempate: extração mais recente


def test_desempate_de_duplicatas_e_deterministico(spark, tmp_path):
    from src.pipeline import transformacoes_spark as ts

    a = _laudo(id_laudo="ld-1", id_analise="an-1", id_snapshot="snap_a")
    b = _laudo(id_laudo="ld-1", id_analise="an-1", id_snapshot="snap_b")
    escolhidos = set()
    for i, ordem in enumerate(([a, b], [b, a])):
        camadas = ts.executar(spark, _raw(tmp_path, ordem, f"r{i}.jsonl"), "exec_det", "2026-09-30T12:00:00Z")
        escolhidos.add(camadas["silver"].collect()[0]["id_snapshot"])
    assert escolhidos == {"snap_b"}  # mesma extração: vence o id_snapshot maior, qualquer que seja a ordem


def test_analise_com_contexto_conflitante_nao_e_montada(spark, tmp_path):
    from src.pipeline import transformacoes_spark as ts

    registros = [
        _laudo(id_laudo="ld-1", id_analise="an-1", uf="SP", id_imagem="img-1"),
        _laudo(id_laudo="ld-2", id_analise="an-1", uf="PR", id_imagem="img-1", motor="Gemini (VLM)",
               confianca=None, confianca_texto="alta"),
        _laudo(id_laudo="ld-3", id_analise="an-2", uf="SP", id_imagem="img-2"),
    ]
    camadas = ts.executar(spark, _raw(tmp_path, registros), "exec_ctx", "2026-09-30T12:00:00Z")
    assert [a["id_analise"] for a in camadas["gold_analises"].collect()] == ["an-2"]
    assert camadas["gold_laudos"].count() == 3  # os laudos continuam disponíveis
    q = {r["metrica"]: r for r in camadas["gold_qualidade"].collect()}
    assert q["analise_contexto_conflitante"]["valor"] == 1
    assert q["analise_contexto_conflitante"]["denominador"] == 2


def test_simulados_de_sementes_diferentes_combinados(spark, tmp_path):
    import gerar_dados_simulados as g

    from src.pipeline import transformacoes_spark as ts

    um, dois = g.gerar(1), g.gerar(2)
    camadas = ts.executar(spark, _raw(tmp_path, um + dois), "exec_seeds", "2026-09-30T12:00:00Z")
    motivos = [r["motivo_rejeicao"] for r in camadas["silver_rejeitados"].collect()]
    assert "conflito_identidade_laudo" not in motivos
    assert motivos.count("duplicado") == 6  # só os 3 duplicados propositais de cada conjunto
    analises_esperadas = len({r["id_analise"] for r in um}) + len({r["id_analise"] for r in dois})
    assert camadas["gold_analises"].count() == analises_esperadas


# --------------------------------------------------------------------------- paridade
def test_paridade_spark_e_fonte_operacional(spark, tmp_path):
    """O mesmo histórico SQLite, pelo Spark e pelo leitor ao vivo: mesmo resultado."""
    import json

    from PIL import Image

    from src import analitica_operacional, historico
    from src.pipeline import ingestao
    from src.pipeline import transformacoes_spark as ts
    from src.tipos import MOTOR_CNN, MOTOR_VLM, Resultado

    def r(motor, condicao, diag, especie, data, confianca=None):
        return Resultado(arquivo="f.jpg", data_hora=data, motor=motor, especie=especie, diagnostico=diag,
                         condicao=condicao, confianca=confianca, confianca_texto="x", agente="Fungo",
                         sintomas="s", manejo="m", modelo_versao="v")

    figura = Image.new("RGB", (4, 4))
    # comparação: CNN indeterminado, VLM saudável -> principal é o VLM
    historico.salvar_analise([r(MOTOR_CNN, "indeterminado", "Indeterminado", "—", "2026-09-01T08:00:00", 0.4),
                              r(MOTOR_VLM, "saudável", "Saudável", "Milho (Zea mays)", "2026-09-01T08:00:03")],
                             figura, b"img-a", "a.jpg", "Comparar os dois motores", 0.6, {"uf": "PR"})
    # comparação concordante, depois reanálise da mesma imagem
    historico.salvar_analise([r(MOTOR_CNN, "doente", "Requeima", "Tomate", "2026-09-02T09:00:00", 0.9),
                              r(MOTOR_VLM, "doente", "Míldio tardio", "Tomateiro", "2026-09-02T09:00:02")],
                             figura, b"img-b", "b.jpg", "Comparar os dois motores", 0.6,
                             {"uf": "SP", "municipio": "Campinas"})
    historico.salvar_analise([r(MOTOR_CNN, "saudável", "Saudável", "Tomate", "2026-09-09T10:00:00", 0.8)],
                             figura, b"img-b", "b.jpg", "Somente CNN especializado", 0.7, None)
    # VLM com texto livre sem sinônimo, só VLM (limiar nulo)
    historico.salvar_analise([r(MOTOR_VLM, "doente", "Infestação de cochonilhas", "Roseira", "2026-09-10T11:00:00")],
                             figura, b"img-c", "c.jpg", "Somente visão generalista", None, None)
    # laudo sem vínculo (histórico antigo)
    historico.salvar(r(MOTOR_CNN, "doente", "Pinta-preta", "Batata", "2026-08-30T07:00:00", 0.95), figura)

    brutos = ingestao.ler_historico_sqlite(historico.CAMINHO_BANCO)
    raw = tmp_path / "laudos.jsonl"
    raw.write_text("".join(json.dumps({**b, "id_snapshot": "snap_p", "extraido_em": "2026-09-30T00:00:00Z"},
                                      ensure_ascii=False) + "\n" for b in brutos), encoding="utf-8")
    camadas = ts.executar(spark, str(raw), "exec_paridade", "2026-09-30T00:00:00Z")
    vivo = analitica_operacional.carregar()

    campos = ("id_analise", "id_imagem", "data_analise_ts", "data_dia", "semana", "uf", "municipio",
              "modo_solicitado", "limiar_cnn", "cultura", "condicao", "diagnostico_normalizado",
              "categoria_problema", "motor_principal", "n_laudos", "comparacao", "condicao_cnn",
              "condicao_vlm", "concordancia_motores", "confianca_cnn", "e_reanalise")

    def normal(registro):
        return {k: (str(registro[k]) if k in ("data_dia", "semana") else registro[k]) for k in campos}

    # O lado Spark é comparado na forma em que vai para o pacote (JSON), que é o
    # que a API consome — `collect()` devolveria datetime em vez do texto ISO.
    spark_analises = sorted((normal(json.loads(x)) for x in ts.para_linhas_json(camadas["gold_analises"])),
                            key=lambda x: x["id_analise"])
    vivo_analises = sorted((normal(a) for a in vivo.analises), key=lambda x: x["id_analise"])
    assert len(vivo_analises) == len(spark_analises) == 4
    divergencias = [(v["id_analise"][:8], k, sp[k], v[k]) for v, sp in zip(vivo_analises, spark_analises)
                    for k in campos if v[k] != sp[k]]
    assert divergencias == []  # (análise, campo, spark, ao vivo)

    campos_laudo = ("id_laudo", "id_analise", "condicao", "motor", "cultura", "diagnostico_normalizado",
                    "categoria_problema", "diagnostico_mapeado", "data_analise_ts")
    spark_laudos = sorted(({k: l[k] for k in campos_laudo}
                           for l in (json.loads(x) for x in ts.para_linhas_json(camadas["gold_laudos"]))),
                          key=lambda x: x["id_laudo"])
    vivo_laudos = sorted(({k: l[k] for k in campos_laudo} for l in vivo.laudos), key=lambda x: x["id_laudo"])
    assert len(vivo_laudos) == len(spark_laudos) == 7
    divergencias = [(v["id_laudo"], k, sp[k], v[k]) for v, sp in zip(vivo_laudos, spark_laudos)
                    for k in campos_laudo if v[k] != sp[k]]
    assert divergencias == []  # (laudo, campo, spark, ao vivo)
