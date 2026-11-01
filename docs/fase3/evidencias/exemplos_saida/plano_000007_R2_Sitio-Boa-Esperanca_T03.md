# AgroSmart — Plano de controle

**Alerta nº 7** · regra **R2 — Plano de controle por persistência** · severidade **ALTA**

| | |
|---|---|
| Local | Sítio Boa Esperança · talhão T03 · Campinas/SP |
| Cultura | Tomate |
| Momento do dado | 2026-10-31T23:59:59-03:00 |
| Gerado em | 2026-11-01T19:48:16-03:00 (micro-batch nº 6) |

## Situação

A praga Ácaro-rajado foi diagnosticada no talhão em 3 dias seguidos (29/10, 30/10, 31/10). A ocorrência deixou de ser pontual: o plano de controle foi acionado.

## Regra que disparou

`SE P ∈ {doença, praga} AND P(D) AND P(D-1) AND P(D-2) AND NOT plano_P_nos_últimos_14d ENTÃO arquivo de alerta + relatório (plano de controle) + e-mail`

## Evidências

| Indicador | Valor |
|---|---|
| categoria | praga |
| diagnostico | Ácaro-rajado |
| dias | 2026-10-29, 2026-10-30, 2026-10-31 |

## Plano de controle

1. [ ] Confirmar o diagnóstico em campo com o engenheiro agrônomo responsável (até 24 h).
2. [ ] Aplicar o manejo indicado: Praga favorecida por tempo quente e seco: elevar a umidade, preservar ácaros predadores e recorrer a acaricida específico apenas se o nível de dano exigir.
3. [ ] Isolar o talhão: higienizar ferramentas e calçados ao sair, evitar trânsito para talhões vizinhos.
4. [ ] Fotografar 10 plantas por talhão em 3 dias e reenviar ao AgroSmart para medir a evolução.
5. [ ] Registrar a aplicação (produto, dose, carência) no caderno de campo.

---
*Gerado automaticamente pelo AgroSmart (regras v3.0.0). O diagnóstico por imagem é uma triagem:
a decisão de aplicar defensivo exige engenheiro agrônomo.*
