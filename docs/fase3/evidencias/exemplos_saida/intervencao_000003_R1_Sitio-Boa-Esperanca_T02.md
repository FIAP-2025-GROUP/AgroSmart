# AgroSmart — Relatório de intervenção

**Alerta nº 3** · regra **R1 — Alerta de infestação** · severidade **ALTA**

| | |
|---|---|
| Local | Sítio Boa Esperança · talhão T02 · Campinas/SP |
| Cultura | Tomate |
| Momento do dado | 2026-10-29T23:59:59-03:00 |
| Gerado em | 2026-11-01T19:48:15-03:00 (micro-batch nº 4) |

## Situação

7 de 22 análises conclusivas (32%) entre 23/10 e 29/10 apontaram planta doente, acima do limite de 30%. Problema mais frequente: Requeima.

## Regra que disparou

`SE análises_conclusivas_7d ≥ 5 AND doentes_7d/análises_7d > 30% AND NOT alerta_R1_nos_últimos_7d ENTÃO arquivo de alerta + relatório de intervenção + e-mail`

## Evidências

| Indicador | Valor |
|---|---|
| analises conclusivas | 22 |
| diagnosticos | Requeima: 7 |
| doentes | 7 |
| janela | 2026-10-23, 2026-10-29 |
| limite | 30.0% |
| proporcao doente | 31.8% |

## Intervenção recomendada

1. [ ] Vistoriar o talhão e confirmar o diagnóstico com o engenheiro agrônomo (até 24 h).
2. [ ] Se confirmado, aplicar o manejo indicado: Manejo indicado para Requeima: Doença de evolução rápida em clima úmido e ameno: eliminar plantas afetadas, reduzir a molhagem foliar e aplicar fungicida sistêmico com urgência.
3. [ ] Reavaliar o talhão com novas fotos em 3 dias.

---
*Gerado automaticamente pelo AgroSmart (regras v3.0.0). O diagnóstico por imagem é uma triagem:
a decisão de aplicar defensivo exige engenheiro agrônomo.*
