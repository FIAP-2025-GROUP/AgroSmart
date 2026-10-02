"""Camada analítica do AgroSmart — Fase 2.

Fluxo: fonte (histórico SQLite ou dados simulados) → snapshot RAW → Bronze →
Silver → Gold → pacote processado → API analítica → dashboard.

Nada aqui é importado pela Fase 1. Os módulos puros (`contratos`, `ingestao`,
`normalizacao`, `pacote`) não dependem de PySpark; apenas
`transformacoes_spark` exige Spark, e ele nunca é carregado pelo servidor web.
"""
