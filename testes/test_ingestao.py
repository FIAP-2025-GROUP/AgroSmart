"""Contrato, ingestão (SQLite legado e simulados), snapshot RAW, normalização e gerador."""

from __future__ import annotations

import json
from collections import Counter, defaultdict

import pytest
from PIL import Image

from src import historico, rotulos
from src.pipeline import contratos as c
from src.pipeline import ingestao
from src.pipeline import normalizacao as n
from src.tipos import MOTOR_CNN, MOTOR_VLM, Resultado


def _resultado(motor: str, condicao: str = "doente", diagnostico: str = "Requeima", confianca=0.91):
    return Resultado(
        arquivo="folha.jpg", data_hora="2026-09-20T10:00:00", motor=motor, especie="Tomate",
        diagnostico=diagnostico, condicao=condicao, confianca=confianca,
        confianca_texto="91.0%" if confianca else "alta (autoavaliada)", agente="Oomiceto",
        sintomas="s", manejo="m", modelo_versao="v",
    )


# --------------------------------------------------------------------------- contrato
def test_contrato_sem_campos_repetidos_e_obrigatorios_presentes():
    nomes = [nome for nome, _ in c.CAMPOS_RAW]
    assert len(nomes) == len(set(nomes))
    assert set(c.CAMPOS_OBRIGATORIOS) <= set(nomes)
    assert {tipo for _, tipo in c.CAMPOS_RAW} == {"string", "double"}
    assert c.registro_vazio().keys() == {nome for nome, _ in c.CAMPOS_LAUDO}


# --------------------------------------------------------------------------- SQLite legado
def test_historico_legado_vira_contrato_sem_blob_e_sem_inventar_identidade():
    # Uma análise no modo comparação da Fase 1 grava duas linhas da mesma foto.
    figura = Image.new("RGB", (8, 8))
    historico.salvar(_resultado(MOTOR_CNN), figura)
    historico.salvar(_resultado(MOTOR_VLM, confianca=None), figura)
    antes = historico.CAMINHO_BANCO.read_bytes()

    registros = ingestao.ler_historico_sqlite(historico.CAMINHO_BANCO, instancia="teste")

    assert len(registros) == 2
    for registro in registros:
        assert set(registro) == {nome for nome, _ in c.CAMPOS_LAUDO}
        assert "miniatura" not in registro
        assert registro["origem_dado"] == c.ORIGEM_REAL
        # Ausência permanece nula — nada de agrupar as duas linhas por suposição.
        for campo in ("id_analise", "id_imagem", "uf", "municipio", "modo_solicitado", "limiar_cnn"):
            assert registro[campo] is None
    assert [r["id_laudo"] for r in registros] == ["teste:laudo:1", "teste:laudo:2"]
    assert registros[1]["confianca"] is None
    # Extração somente leitura: o banco operacional não muda.
    assert historico.CAMINHO_BANCO.read_bytes() == antes


def test_historico_ausente_devolve_lista_vazia(tmp_path):
    assert ingestao.ler_historico_sqlite(tmp_path / "nao_existe.db") == []


# --------------------------------------------------------------------------- snapshot RAW
def test_snapshot_grava_manifesto_com_contagens_e_hash(tmp_path):
    registros = [{**c.registro_vazio(), "id_laudo": "a", "origem_dado": "simulado"},
                 {**c.registro_vazio(), "id_laudo": "b", "origem_dado": "real", "campo_estranho": 1}]
    pasta = ingestao.criar_snapshot(registros, fontes=[{"tipo": "teste"}], destino=tmp_path)

    manifesto = ingestao.validar_snapshot(pasta)
    assert manifesto["quantidade_registros"] == 2
    assert manifesto["registros_por_origem"] == {"real": 1, "simulado": 1}
    assert manifesto["versao_schema"] == c.VERSAO_SCHEMA
    linhas = [json.loads(x) for x in (pasta / "laudos.jsonl").read_text(encoding="utf-8").splitlines()]
    assert list(linhas[0]) == c.NOMES_CAMPOS_RAW  # só o contrato, na ordem do contrato
    assert all(linha["id_snapshot"] == manifesto["id_snapshot"] for linha in linhas)
    assert not list(tmp_path.glob(".tmp_*"))

    with open(pasta / "laudos.jsonl", "a", encoding="utf-8") as arquivo:
        arquivo.write("{}\n")
    with pytest.raises(ingestao.SnapshotInvalidoError):
        ingestao.validar_snapshot(pasta)


def test_fonte_simulada_recusa_registro_que_se_diz_real(tmp_path):
    arquivo = tmp_path / "sim.jsonl"
    arquivo.write_text(json.dumps({"id_laudo": "x", "origem_dado": "real"}) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="origem_dado"):
        ingestao.ler_simulados(arquivo)


def test_exportar_snapshot_ambos_junta_real_e_simulado(tmp_path):
    import exportar_snapshot
    import gerar_dados_simulados

    historico.salvar(_resultado(MOTOR_CNN), Image.new("RGB", (8, 8)))
    simulados = tmp_path / "sim.jsonl"
    simulados.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n"
                                 for r in gerar_dados_simulados.gerar()[:20]), encoding="utf-8")

    pasta = exportar_snapshot.exportar("ambos", banco=historico.CAMINHO_BANCO, simulados=simulados,
                                       destino=tmp_path / "raw")
    manifesto = ingestao.validar_snapshot(pasta)
    assert manifesto["registros_por_origem"] == {"real": 1, "simulado": 20}
    assert {f["origem_dado"] for f in manifesto["fontes"]} == {"real", "simulado"}


# --------------------------------------------------------------------------- normalização
@pytest.mark.parametrize(("condicao", "diagnostico", "agente", "esperado"), [
    ("doente", "Requeima", "Oomiceto", ("Requeima", "doenca", True)),
    ("doente", "Míldio tardio", None, ("Requeima", "doenca", True)),
    ("doente", "Requeima (Phytophthora infestans)", None, ("Requeima", "doenca", True)),
    ("doente", "Ácaro-rajado", "Praga (Tetranychus urticae)", ("Ácaro-rajado", "praga", True)),
    ("doente", "Infestação de cochonilhas", "—", ("Infestação de cochonilhas", "praga", False)),
    ("doente", "Deficiência de nitrogênio", "Deficiência nutricional", ("Deficiência de nitrogênio", "outra_anomalia", True)),
    ("doente", "Lesões de origem não identificada", "—", ("Lesões de origem não identificada", "outra_anomalia", False)),
    ("saudavel", "Saudável", "—", ("Saudável", "saudavel", True)),
    ("indeterminado", "Não é uma planta", "—", ("Indeterminado", "indeterminado", True)),
])
def test_normalizacao_de_diagnostico(condicao, diagnostico, agente, esperado):
    assert n.normalizar_diagnostico(condicao, diagnostico, agente) == esperado


@pytest.mark.parametrize(("cultura", "especie", "esperado"), [
    (None, "Tomateiro (Solanum lycopersicum)", ("Tomate", True)),
    (None, "Macieira", ("Maçã", True)),
    (None, "Batata-doce", ("Batata-doce", True)),  # o sinônimo mais longo vence
    (None, "Hibisco", ("Hibisco", False)),           # texto livre preservado, não forçado
    (None, "—", (None, False)),
])
def test_normalizacao_de_cultura(cultura, especie, esperado):
    assert n.normalizar_cultura(cultura, especie) == esperado


def test_normalizacao_de_condicao_e_motor():
    assert n.normalizar_condicao("saudável") == "saudavel"
    assert n.normalizar_condicao("talvez") is None
    assert n.normalizar_motor(MOTOR_CNN) == "cnn"
    assert n.normalizar_motor(MOTOR_VLM) == "vlm"
    assert n.normalizar_motor("Motor desconhecido") is None


def test_todas_as_classes_doentes_do_cnn_tem_sinonimo():
    for classe in rotulos.CLASSES:
        if classe["condicao"] == rotulos.CONDICAO_DOENTE:
            nome, _, mapeado = n.normalizar_diagnostico("doente", classe["diagnostico"], classe["agente"])
            assert mapeado and nome == classe["diagnostico"]


# --------------------------------------------------------------------------- gerador simulado
def test_gerador_e_reprodutivel_e_usa_so_classes_reais():
    import gerar_dados_simulados as g

    primeiro, segundo = g.gerar(123), g.gerar(123)
    assert primeiro == segundo
    assert g.gerar(124) != primeiro

    validos = [r for r in primeiro if r.get("id_laudo") and "invalido" not in r["id_laudo"]]
    assert {r["origem_dado"] for r in primeiro} == {"simulado"}

    pares_catalogo = {(k["cultura"], k["diagnostico"]) for k in rotulos.CLASSES}
    for r in validos:
        if r["motor"] == MOTOR_CNN:
            if r["condicao"] == rotulos.CONDICAO_INDETERMINADA:
                assert r["confianca"] < r["limiar_cnn"]
            else:
                assert (r["especie_original"], r["diagnostico_original"]) in pares_catalogo
                assert r["confianca"] >= r["limiar_cnn"]
        if r["motor"] == MOTOR_VLM:
            assert r["confianca"] is None  # autoavaliação não vira número

    por_analise = defaultdict(list)
    for r in validos:
        por_analise[r["id_analise"]].append(r)
    assert any(len(v) == 2 for v in por_analise.values())  # modo comparação
    imagens = Counter(v[0]["id_imagem"] for v in por_analise.values() if v[0]["id_imagem"])
    assert any(n_ > 1 for n_ in imagens.values())  # reanálise da mesma imagem
    semanas = {r["data_analise"][:10] for r in validos}
    assert min(semanas) >= "2026-06-01" and max(semanas) <= "2026-08-23"


def test_sementes_diferentes_nao_colidem_ids():
    import gerar_dados_simulados as g

    um, dois = g.gerar(1), g.gerar(2)
    for campo in ("id_laudo", "id_analise", "id_imagem"):
        ids_um = {r[campo] for r in um if r.get(campo)}
        ids_dois = {r[campo] for r in dois if r.get(campo)}
        assert ids_um and ids_dois and not ids_um & ids_dois, campo
