"""Fase 3: regras de decisão, ingestão incremental com checkpoint e ações."""

from __future__ import annotations

import json
import shutil
from datetime import date

import pytest

import gerar_dados_fase3 as gerador
from src.automacao import acoes, armazenamento as db, contratos as c, painel, regras
from src.automacao.motor import Monitor


@pytest.fixture
def amb(tmp_path):
    ambiente = c.Ambiente(entrada=tmp_path / "entrada", base=tmp_path / "fase3")
    ambiente.criar_pastas()
    return ambiente


@pytest.fixture
def monitor(amb):
    m = Monitor(amb)
    yield m
    m.fechar()


def _contar(monitor, tabela, onde="1=1"):
    return monitor.conexao.execute(f"SELECT COUNT(*) FROM {tabela} WHERE {onde}").fetchone()[0]


# --------------------------------------------------------------------------- #
# Regras (funções puras) — tabela de decisão
# --------------------------------------------------------------------------- #
def _analise(condicao, dia="2026-10-29", diag="Requeima", categoria="doenca", chave=None):
    return {"chave": chave or f"{dia}-{condicao}-{id(object())}", "id_analise": None, "condicao": condicao,
            "diagnostico": diag if condicao == "doente" else "Saudável", "categoria": categoria,
            "dia": dia, "instante": f"{dia}T10:00:00-03:00", "motor": "cnn", "manejo": None,
            "propriedade": "P", "talhao": "T1", "cultura": "Tomate", "uf": "SP", "municipio": "X"}


@pytest.mark.parametrize(("doentes", "saudaveis", "ja", "dispara"), [
    (2, 2, False, False),   # 4 análises < mínimo de 5
    (3, 7, False, False),   # exatamente 30% não passa (é "> 30%")
    (4, 6, False, True),    # 40%
    (4, 6, True, False),    # NOT alerta recente
])
def test_r1_tabela_de_decisao(doentes, saudaveis, ja, dispara):
    janela = [_analise("doente") for _ in range(doentes)] + [_analise("saudavel") for _ in range(saudaveis)]
    resultado = regras.r1_infestacao(date(2026, 10, 29), janela, ja)
    assert (resultado is not None) is dispara
    if dispara:
        assert resultado.acoes == (regras.ACAO_ARQUIVO, regras.ACAO_RELATORIO, regras.ACAO_EMAIL)


def test_r1_indeterminadas_fora_do_denominador():
    janela = [_analise("doente")] * 2 + [_analise("saudavel")] * 3 + [_analise("indeterminado")] * 5
    assert regras.r1_infestacao(date(2026, 10, 29), janela, False).evidencias["analises_conclusivas"] == 5


def test_laudo_principal_conta_comparacao_uma_vez():
    cnn = {**_analise("doente"), "id_analise": "a1", "chave": "cnn", "motor": "cnn"}
    vlm = {**_analise("doente"), "id_analise": "a1", "chave": "vlm", "motor": "vlm"}
    assert [a["chave"] for a in regras.analises([vlm, cnn])] == ["cnn"]


def test_r2_exige_dias_seguidos_e_respeita_plano_recente():
    d = date(2026, 10, 30)
    ex = _analise("doente")
    seguidos = {date(2026, 10, 28): {"Requeima": ex}, date(2026, 10, 29): {"Requeima": ex}, d: {"Requeima": ex}}
    assert len(regras.r2_persistencia(d, seguidos, lambda _: False)) == 1
    assert regras.r2_persistencia(d, seguidos, lambda _: True) == []
    com_falha = {date(2026, 10, 27): {"Requeima": ex}, date(2026, 10, 29): {"Requeima": ex}, d: {"Requeima": ex}}
    assert regras.r2_persistencia(d, com_falha, lambda _: False) == []


def _leitura(solo=30.0, temp=20.0, ur=70.0, pragas=2, cultura="Tomate", ident="l1"):
    return {"id_leitura": ident, "instante": "2026-10-29T06:00:00-03:00", "propriedade": "P", "talhao": "T1",
            "cultura": cultura, "uf": "SP", "municipio": "X", "umidade_solo_pct": solo, "temperatura_c": temp,
            "umidade_ar_pct": ur, "chuva_mm": 0.0, "pragas_armadilha": pragas}


@pytest.mark.parametrize(("solo", "chuva", "ligada", "comando", "severidade"), [
    (24.0, 0.0, False, "ligar", "media"),
    (14.0, 0.0, False, "ligar", "critica"),
    (24.0, 6.0, False, None, None),        # choveu: NOT liga
    (24.0, 0.0, True, None, None),         # já ligada
    (30.0, 0.0, True, None, None),         # histerese: entre 25% e 35% mantém
    (35.0, 0.0, True, "desligar", "informativa"),
    (28.0, 5.0, True, "desligar", "informativa"),  # OR chuva
])
def test_r3_irrigacao_com_histerese(solo, chuva, ligada, comando, severidade):
    d = regras.r3_irrigacao(_leitura(solo=solo), chuva, ligada)
    assert (d.comando_irrigacao if d else None) == comando
    assert (d.severidade if d else None) == severidade
    if d and severidade == "critica":
        assert regras.ACAO_EMAIL in d.acoes


def test_r4_precisa_de_duas_leituras_seguidas_e_cultura_suscetivel():
    umida = _leitura(temp=18, ur=93)
    assert regras.r4_requeima([umida], False) is None
    assert regras.r4_requeima([_leitura(temp=18, ur=80), umida], False) is None
    assert regras.r4_requeima([umida, {**umida, "id_leitura": "l2"}], False) is not None
    milho = _leitura(temp=18, ur=93, cultura="Milho")
    assert regras.r4_requeima([milho, milho], False) is None
    assert regras.r4_requeima([umida, umida], True) is None


@pytest.mark.parametrize(("pragas", "laudo", "dispara", "email"), [
    (9, True, False, False), (12, False, False, False), (12, True, True, False), (20, False, True, True),
])
def test_r5_ou_logico(pragas, laudo, dispara, email):
    d = regras.r5_pragas(_leitura(pragas=pragas), laudo, False)
    assert (d is not None) is dispara
    if d:
        assert (regras.ACAO_EMAIL in d.acoes) is email


# --------------------------------------------------------------------------- #
# Ingestão incremental
# --------------------------------------------------------------------------- #
def test_arquivo_crescendo_le_so_linhas_novas_e_espera_linha_completa(amb, monitor):
    arquivo = amb.entrada / "sensores_teste.csv"
    leituras = gerador.leituras_do_dia(1)
    arquivo.write_text(gerador._csv(leituras[:4], cabecalho=True), encoding="utf-8")
    assert monitor.ciclo().novos == 4

    texto = gerador._csv(leituras[4:6], cabecalho=False)
    with open(arquivo, "a", encoding="utf-8") as f:
        f.write(texto[:-10])  # segunda linha ainda pela metade
    assert monitor.ciclo().novos == 1
    with open(arquivo, "a", encoding="utf-8") as f:
        f.write(texto[-10:])
    assert monitor.ciclo().novos == 1
    assert monitor.ciclo().id_lote is None  # nada novo: nenhum lote
    ponto = monitor.conexao.execute("SELECT linhas_lidas FROM arquivos").fetchone()[0]
    assert ponto == 7  # cabeçalho + 6


def test_reenvio_nao_dispara_acao_de_novo(amb, monitor):
    for n in range(1, 5):
        gerador.publicar_dia(amb.entrada, n)
        monitor.ciclo()
    alertas, acoes_ = _contar(monitor, "alertas"), _contar(monitor, "acoes")
    gerador.reenviar(amb.entrada, 4)
    resumo = monitor.ciclo()
    assert resumo.novos == 0 and resumo.duplicados > 0 and resumo.alertas == []
    assert (_contar(monitor, "alertas"), _contar(monitor, "acoes")) == (alertas, acoes_)


def test_arquivo_reescrito_e_relido_sem_duplicar(amb, monitor):
    gerador.publicar_dia(amb.entrada, 1)
    monitor.ciclo()
    laudos = amb.entrada / "laudos_2026-10-26.jsonl"
    linhas = laudos.read_text(encoding="utf-8").splitlines()
    laudos.write_text("\n".join(reversed(linhas)) + "\n", encoding="utf-8")  # mesmo conteúdo, outra ordem
    resumo = monitor.ciclo()
    assert resumo.arquivos[0]["reiniciado"] and resumo.novos == 0 and resumo.duplicados == len(linhas)


def test_linhas_invalidas_sao_rejeitadas_com_motivo(amb, monitor):
    (amb.entrada / "laudos_x.jsonl").write_text('{"id_laudo": "a"\n{"id_laudo": "b"}\n', encoding="utf-8")
    (amb.entrada / "sensores_x.csv").write_text(
        ",".join(c.NOMES_SENSOR) + "\nx,2026-10-26T06:00,s,SP,C,P,T,Tomate,140,20,70,0,1\n", encoding="utf-8")
    (amb.entrada / ".sensores_y.csv.tmp").write_text("ignorado\n", encoding="utf-8")
    resumo = monitor.ciclo()
    assert resumo.rejeitados == 3 and resumo.novos == 0
    motivos = {r[0] for r in monitor.conexao.execute("SELECT motivos FROM rejeitados")}
    assert "json_invalido" in motivos and "umidade_solo_pct_fora_da_faixa" in motivos
    assert any("data_ausente" in m for m in motivos)


def test_csv_ponto_e_virgula_com_decimal_virgula(amb, monitor):
    leitura = gerador.leituras_do_dia(1)[0]
    valores = [str(leitura[k]).replace(".", ",") if isinstance(leitura[k], float) else str(leitura[k])
               for k in c.NOMES_SENSOR]
    (amb.entrada / "sensores_br.csv").write_text(";".join(c.NOMES_SENSOR) + "\n" + ";".join(valores) + "\n",
                                                 encoding="utf-8-sig")
    assert monitor.ciclo().novos == 1
    assert monitor.conexao.execute("SELECT umidade_solo_pct FROM leituras").fetchone()[0] == leitura["umidade_solo_pct"]


# --------------------------------------------------------------------------- #
# Cenário completo e recuperação
# --------------------------------------------------------------------------- #
def test_cenario_de_sete_dias(amb, monitor):
    for n in range(1, 8):
        gerador.publicar_dia(amb.entrada, n)
        monitor.ciclo()
    por_regra = dict(monitor.conexao.execute("SELECT regra, COUNT(*) FROM alertas GROUP BY regra").fetchall())
    assert por_regra == {"R1": 1, "R2": 2, "R3": 4, "R4": 2, "R5": 3}
    assert _contar(monitor, "acoes", "status != 'executada'") == 0
    assert len(list(amb.emails.glob("*.eml"))) == 5
    assert len(list(amb.relatorios.glob("*.md"))) == 3
    # Fazenda Santa Clara é o controle: nenhuma regra dispara lá.
    assert _contar(monitor, "alertas", "propriedade = 'Fazenda Santa Clara'") == 0
    assert (amb.alertas / "alertas.csv").read_text(encoding="utf-8-sig").count("\n") == 13


def test_todos_de_uma_vez_da_o_mesmo_resultado(amb, monitor):
    for n in range(1, 8):
        gerador.publicar_dia(amb.entrada, n)
    monitor.ciclo()
    assert _contar(monitor, "alertas") == 12


def test_lote_interrompido_e_reavaliado_e_acao_pendente_executada(amb, monitor):
    for n in range(1, 5):
        gerador.publicar_dia(amb.entrada, n)
    monitor.ciclo()
    # Simula queda: alertas do último lote somem e o lote volta a "não avaliado".
    conexao = monitor.conexao
    with db.transacao(conexao):
        conexao.execute("DELETE FROM acoes")
        conexao.execute("DELETE FROM alertas")
        conexao.execute("DELETE FROM irrigacao")
        conexao.execute("UPDATE lotes SET concluido_em = NULL")
    shutil.rmtree(amb.saida)
    resumo = monitor.ciclo()
    assert len(resumo.alertas) == _contar(monitor, "alertas") > 0
    assert _contar(monitor, "acoes", "status = 'pendente'") == 0
    assert list(amb.emails.glob("*.eml"))


def test_email_por_smtp_quando_configurado(amb, monitor, monkeypatch):
    enviados = []

    class SMTPFalso:
        def __init__(self, host, porta, timeout):
            self.host = host
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def starttls(self):
            pass
        def login(self, usuario, senha):
            pass
        def send_message(self, mensagem):
            enviados.append(mensagem)

    monkeypatch.setattr(acoes.smtplib, "SMTP", SMTPFalso)
    monkeypatch.setenv("AGROSMART_SMTP_HOST", "smtp.exemplo")
    for n in range(1, 5):
        gerador.publicar_dia(amb.entrada, n)
    monitor.ciclo()
    assert len(enviados) == 1 and "infestação" in enviados[0]["Subject"]
    assert enviados[0].get_payload()[1].get_filename().endswith(".md")  # relatório anexo


# --------------------------------------------------------------------------- #
# Painel
# --------------------------------------------------------------------------- #
def test_painel_e_conteudo_das_acoes(amb, monitor, monkeypatch):
    monkeypatch.setattr(c.Ambiente, "padrao", classmethod(lambda cls: amb))
    from fastapi.testclient import TestClient

    import servidor

    cliente = TestClient(servidor.app)
    assert cliente.get("/api/automacao").json()["totais"]["alertas"] == 0
    for n in range(1, 5):
        gerador.publicar_dia(amb.entrada, n)
    monitor.ciclo()
    dados = cliente.get("/api/automacao").json()
    assert dados["disponivel"] and dados["totais"]["alertas"] == 4
    email = next(x for a in dados["alertas"] for x in a["acoes"] if x["tipo"] == "email")
    texto = cliente.get(f"/api/automacao/acao/{email['id']}").json()["texto"]
    assert "Subject:" in texto and "Requeima" in texto
    assert cliente.get("/api/automacao/acao/99999").status_code == 404
    # Destino adulterado fora de saida/ não é servido.
    monitor.conexao.execute("UPDATE acoes SET destino = '../../etc/passwd' WHERE id = ?", (email["id"],))
    assert cliente.get(f"/api/automacao/acao/{email['id']}").status_code == 404
    assert cliente.get("/automacao").status_code == 200


def test_painel_sem_banco(tmp_path):
    vazio = c.Ambiente(entrada=tmp_path / "e", base=tmp_path / "b")
    assert painel.consultar(vazio)["disponivel"] is False


def test_gerador_e_deterministico():
    assert gerador.laudos_do_dia(3) == gerador.laudos_do_dia(3)
    assert all(json.loads(x)["origem_dado"] == "simulado" for x in gerador.laudos_do_dia(1))
    assert painel.consultar  # módulo do painel importa sem TensorFlow/Spark


# --------------------------------------------------------------------------- #
# Robustez (achados da revisão de QA)
# --------------------------------------------------------------------------- #
def _csv_leitura(amb, nome, **mudancas):
    leitura = {**gerador.leituras_do_dia(1)[0], **mudancas}
    (amb.entrada / nome).write_text(gerador._csv([leitura], cabecalho=True), encoding="utf-8")


def test_monitor_sobrevive_a_erro_no_ciclo(amb, monitor, monkeypatch):
    chamadas = []

    def ciclo_que_falha():
        chamadas.append(1)
        raise PermissionError("arquivo aberto no Excel")

    monkeypatch.setattr(monitor, "ciclo", ciclo_que_falha)
    monitor.rodar(intervalo=0, ciclos=2)  # não propaga
    assert len(chamadas) == 2


def test_cultura_vazia_e_rejeitada_e_nao_some_como_duplicado(amb, monitor):
    _csv_leitura(amb, "sensores_na.csv", cultura="NA")
    resumo = monitor.ciclo()
    assert (resumo.novos, resumo.duplicados, resumo.rejeitados) == (0, 0, 1)
    assert "cultura_ausente" in monitor.conexao.execute("SELECT motivos FROM rejeitados").fetchone()[0]


def test_pragas_fracionadas_e_ids_com_controle_sao_rejeitados(amb, monitor):
    _csv_leitura(amb, "sensores_a.csv", pragas_armadilha=4.9)
    _csv_leitura(amb, "sensores_b.csv", id_leitura="x\x01y")
    assert monitor.ciclo().rejeitados == 2


def test_ponto_e_virgula_com_ponto_decimal_nao_multiplica(amb, monitor):
    leitura = gerador.leituras_do_dia(1)[0]
    valores = [str(leitura[k]) for k in c.NOMES_SENSOR]
    valores[c.NOMES_SENSOR.index("chuva_mm")] = "1.5"
    (amb.entrada / "sensores_pv.csv").write_text(";".join(c.NOMES_SENSOR) + "\n" + ";".join(valores) + "\n",
                                                 encoding="utf-8")
    monitor.ciclo()
    assert monitor.conexao.execute("SELECT chuva_mm FROM leituras").fetchone()[0] == 1.5


def test_laudo_com_separador_unicode_no_texto_nao_quebra_a_linha(amb, monitor):
    laudo = json.loads(gerador.laudos_do_dia(1)[0])
    laudo["manejo"] = "linha 1 linha 2"
    (amb.entrada / "laudos_u.jsonl").write_text(json.dumps(laudo, ensure_ascii=False) + "\n", encoding="utf-8")
    assert monitor.ciclo().novos == 1


def test_csv_em_cp1252_e_lido_sem_corromper_acentos(amb, monitor):
    leitura = gerador.leituras_do_dia(1)[0]
    (amb.entrada / "sensores_ansi.csv").write_bytes(gerador._csv([leitura], cabecalho=True).encode("cp1252"))
    monitor.ciclo()
    assert monitor.conexao.execute("SELECT propriedade FROM leituras").fetchone()[0] == leitura["propriedade"]


def test_dois_monitores_nao_executam_a_mesma_acao_duas_vezes(amb, monitor):
    from src.automacao import acoes as executor

    for n in range(1, 5):
        gerador.publicar_dia(amb.entrada, n)
    outro = Monitor(amb)
    try:
        # O primeiro ingere e grava as ações; antes de executar, o segundo reserva todas.
        monitor.conexao.execute("UPDATE acoes SET status = 'pendente'")
        monitor.ciclo()
        n_email = _contar(monitor, "acoes", "tipo = 'email'")
        monitor.conexao.execute("UPDATE acoes SET status = 'pendente'")
        primeira = executor.executar_pendentes(amb, outro.conexao)
        segunda = executor.executar_pendentes(amb, monitor.conexao)
        assert len([a for a in primeira if a["tipo"] == "email"]) == n_email and segunda == []
    finally:
        outro.fechar()


def test_smtp_fora_do_ar_e_tentado_de_novo(amb, monitor, monkeypatch):
    estado = {"falhas": 1, "enviados": 0}

    class SMTPInstavel:
        def __init__(self, *a, **k):
            if estado["falhas"]:
                estado["falhas"] -= 1
                raise ConnectionRefusedError("SMTP fora do ar")
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def starttls(self):
            pass
        def send_message(self, m):
            estado["enviados"] += 1

    monkeypatch.setattr(acoes.smtplib, "SMTP", SMTPInstavel)
    monkeypatch.setenv("AGROSMART_SMTP_HOST", "smtp.exemplo")
    for n in range(1, 5):
        gerador.publicar_dia(amb.entrada, n)
    monitor.ciclo()
    linha = monitor.conexao.execute("SELECT status, destino, tentativas FROM acoes WHERE tipo = 'email'").fetchone()
    assert linha["status"] == "pendente" and linha["destino"].endswith(".eml") and linha["tentativas"] == 1
    monitor.ciclo()  # nenhum dado novo, mas a ação pendente é tentada de novo
    assert estado["enviados"] == 1
    assert _contar(monitor, "acoes", "tipo = 'email' AND status = 'executada'") == 1


def test_leitura_atrasada_nao_reverte_a_irrigacao(amb, monitor):
    base = {**gerador.leituras_do_dia(1)[0], "propriedade": "P", "talhao": "T9", "cultura": "Milho", "chuva_mm": 0}
    seca = {**base, "id_leitura": "l-10h", "instante": "2026-10-26T10:00:00-03:00", "umidade_solo_pct": 20.0}
    (amb.entrada / "sensores_1.csv").write_text(gerador._csv([seca], cabecalho=True), encoding="utf-8")
    monitor.ciclo()
    umida_antiga = {**base, "id_leitura": "l-08h", "instante": "2026-10-26T08:00:00-03:00", "umidade_solo_pct": 36.0}
    (amb.entrada / "sensores_2.csv").write_text(gerador._csv([umida_antiga], cabecalho=True), encoding="utf-8")
    monitor.ciclo()
    assert monitor.conexao.execute("SELECT ligada FROM irrigacao WHERE talhao = 'T9'").fetchone()[0] == 1


def test_painel_com_caminho_contendo_cerquilha(tmp_path):
    amb = c.Ambiente(entrada=tmp_path / "FIAP #3" / "e", base=tmp_path / "FIAP #3" / "b")
    m = Monitor(amb)
    try:
        gerador.publicar_dia(amb.entrada, 1)
        m.ciclo()
    finally:
        m.fechar()
    assert painel.consultar(amb)["totais"]["laudos"] > 0
    assert not (tmp_path / "FIAP ").exists()


def test_recomendacao_informativa_nao_esconde_alerta_vigente():
    base = {"propriedade": "P", "talhao": "T"}
    alertas = [
        {**base, "id": 3, "regra": "R3", "severidade": "informativa", "instante_evento": "2026-10-31T06:00"},
        {**base, "id": 2, "regra": "R1", "severidade": "alta", "instante_evento": "2026-10-30T23:59"},
        {**base, "id": 1, "regra": "R3", "severidade": "media", "instante_evento": "2026-10-29T06:00"},
    ]
    assert [a["id"] for a in painel._recomendacoes(alertas)] == [2]
