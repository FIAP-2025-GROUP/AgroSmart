"""Monta o .zip da entrega.

    py -3.12 ferramentas/empacotar_entrega.py

O pacote é organizado para que o avaliador veja os quatro itens da rubrica na
primeira tela, sem precisar navegar:

    paulo_..._fase1_atividade.docx    relatório técnico (15%)
    LINK_DO_VIDEO.txt                 vídeo (15%)
    exportacoes/                      exportação estruturada (20%)
    projeto/                          protótipo funcional (50%)
    LEIA-ME.txt                       mapa do pacote

O `.env` NUNCA entra: ele carrega a chave real do Gemini e ficaria legível
para quem receber o arquivo. Só o `.env.example`, sem valor real, é embarcado.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
DESTINO = RAIZ / "entrega"
RELATORIO = RAIZ / "relatorio" / "paulo_sergio_morais_RM553012_3ESOR_fase1_atividade.docx"

# Pastas e arquivos que não vão no pacote, por motivo.
PASTAS_FORA = {
    ".venv",            # ambiente local, ~800 MB
    "__pycache__",
    ".git",
    ".claude",
    "entrega",          # o próprio destino
    "exportacoes",      # vai na raiz do pacote; a pasta é recriada na execução
    "node_modules",
}
ARQUIVOS_FORA = {
    ".env",             # SEGREDO: chave real do Gemini
    ".env.local",
    "historico.db",     # pode conter fotos pessoais; o app gera um novo vazio
}
SUFIXOS_FORA = {".pyc", ".zip", ".pdf"}

AVISO_VIDEO = """\
VÍDEO DA APLICAÇÃO — AgroSmart
Paulo Sergio Morais · RM 553012 · 3ESOR

Link: «COLE AQUI O LINK DO VÍDEO»

Duração: até 5 minutos.
Sugestão de roteiro:
  0:00  O problema — diagnóstico no campo demora dias para chegar ao agrônomo
  0:30  A solução em dois motores e por que dois
  1:15  Demonstração: foto de folha doente, laudo completo na tela
  2:30  Foto ruim de propósito: o sistema recusa o diagnóstico e diz o que falta
  3:15  Histórico e exportação em CSV e JSON abertos no Excel
  4:15  Aplicabilidade no agronegócio e limitações
"""


def _mapa_do_pacote(nome_relatorio: str) -> str:
    return f"""\
ENTREGA — AgroSmart
Paulo Sergio Morais · RM 553012 · 3ESOR
Fase 1 — Atividade de visão computacional

CONTEÚDO DESTE PACOTE
---------------------
{nome_relatorio}
    Relatório técnico completo (11 páginas): processo de desenvolvimento,
    tecnologias, imagens de exemplo com laudos reais e aplicabilidade no
    agronegócio.

LINK_DO_VIDEO.txt
    Endereço do vídeo de apresentação.

exportacoes/
    Resultados exportados pelo sistema em CSV e JSON, 17 campos por laudo.
    O CSV usa separador ';' e encoding utf-8-sig — abre direto no Excel em
    português, com as colunas separadas e os acentos corretos.

projeto/
    Código-fonte completo do protótipo. Para executar, veja projeto/README.md.
    Resumo:  py -3.12 -m venv .venv
             .venv\\Scripts\\python.exe -m pip install -r requirements.txt
             copy .env.example .env      (e cole a chave gratuita do Gemini)
             .venv\\Scripts\\python.exe servidor.py
    Depois abra http://localhost:8000 e arraste a foto de uma planta.

OBSERVAÇÃO SOBRE A CHAVE DE API
-------------------------------
O arquivo .env com a chave pessoal do Gemini NÃO acompanha este pacote, por
segurança. O modelo sem valor real (.env.example) está em projeto/. A chave
gratuita é obtida em https://aistudio.google.com/apikey, sem cartão de crédito.
Sem chave, o motor CNN continua funcionando normalmente.
"""


def _deve_entrar(caminho: Path) -> bool:
    partes = set(caminho.relative_to(RAIZ).parts)
    if partes & PASTAS_FORA:
        return False
    if caminho.name in ARQUIVOS_FORA:
        return False
    # Arquivo de bloqueio que o Word cria enquanto o .docx está aberto.
    if caminho.name.startswith("~$"):
        return False
    if caminho.suffix.lower() in SUFIXOS_FORA:
        return False
    # O relatório entra na raiz do pacote, não dentro de projeto/.
    return caminho != RELATORIO


def empacotar() -> Path:
    if not RELATORIO.exists():
        raise SystemExit(f"Relatório não encontrado em {RELATORIO}")

    DESTINO.mkdir(exist_ok=True)
    saida = DESTINO / f"{RELATORIO.stem}.zip"

    with zipfile.ZipFile(saida, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as pacote:
        # -------------------------------------------------- raiz do pacote
        pacote.write(RELATORIO, RELATORIO.name)
        pacote.writestr("LEIA-ME.txt", _mapa_do_pacote(RELATORIO.name))
        pacote.writestr("LINK_DO_VIDEO.txt", AVISO_VIDEO)

        for arquivo in sorted((RAIZ / "exportacoes").glob("*.*")):
            if arquivo.suffix.lower() in {".csv", ".json"}:
                pacote.write(arquivo, f"exportacoes/{arquivo.name}")

        # ------------------------------------------------------- projeto/
        for arquivo in sorted(RAIZ.rglob("*")):
            if arquivo.is_dir() or not _deve_entrar(arquivo):
                continue
            pacote.write(arquivo, f"projeto/{arquivo.relative_to(RAIZ).as_posix()}")

    return saida


if __name__ == "__main__":
    caminho = empacotar()
    with zipfile.ZipFile(caminho) as pacote:
        nomes = pacote.namelist()
        tamanho = sum(i.file_size for i in pacote.infolist())

    print(f"\n  {caminho}")
    print(f"  {len(nomes)} arquivos · {caminho.stat().st_size / 1024:.0f} KB "
          f"comprimidos (de {tamanho / 1024:.0f} KB)\n")

    raiz = sorted({n.split("/")[0] + ("/" if "/" in n else "") for n in nomes})
    for nome in raiz:
        print(f"    {nome}")

    vazados = [n for n in nomes if n.endswith(".env") or n.endswith("historico.db")]
    print("\n  " + ("ALERTA: segredo no pacote -> " + ", ".join(vazados)
                    if vazados else "Sem .env e sem banco pessoal no pacote."))
