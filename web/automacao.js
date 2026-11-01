/* AgroSmart — página de automação (Fase 3).
   Consulta /api/automacao a cada 3 s; alertas novos entram no topo com destaque. */
"use strict";

const SEV = {
  critica: ["CRÍTICA", "var(--sev-critica)"],
  alta: ["ALTA", "var(--sev-alta)"],
  media: ["MÉDIA", "var(--sev-media)"],
  informativa: ["INFO", "var(--sev-informativa)"],
};
const ACAO = { arquivo_alerta: "📄 arquivo de alerta", relatorio: "📋 relatório", irrigacao: "💧 irrigação", email: "✉️ e-mail" };
const INTERVALO_MS = 3000;

const $ = (id) => document.getElementById(id);
const vistos = new Set();
let primeiraCarga = true;

function esc(texto) {
  return String(texto ?? "—").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}
const num = (v, casas = 0) => (v == null ? "—" : Number(v).toLocaleString("pt-BR", { minimumFractionDigits: casas, maximumFractionDigits: casas }));
function quando(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleString("pt-BR", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit", timeZone: "America/Sao_Paulo" });
}

function monitor(m) {
  const el = $("monitor");
  el.classList.toggle("ativo", !!m.ativo);
  el.querySelector("span").textContent = m.ativo
    ? `monitor ativo · varredura a cada ${num(m.intervalo_s)} s`
    : m.ultima_varredura ? `monitor parado · última varredura ${quando(m.ultima_varredura)}` : "monitor não iniciado";
}

function cards(t) {
  const lista = [
    [t.arquivos, "arquivos monitorados", "com checkpoint"],
    [t.lotes, "micro-batches", "lotes com dados novos"],
    [t.laudos + t.leituras, "registros processados", `${num(t.laudos)} laudos · ${num(t.leituras)} leituras`],
    [t.duplicados, "já processados, ignorados", "não disparam ação de novo"],
    [t.rejeitados, "rejeitados", "linha inválida, com motivo"],
    [t.alertas, "alertas", "decisões das regras"],
    [t.acoes_executadas, "ações executadas", `${num(t.emails)} e-mails · ${num(t.relatorios)} relatórios`],
  ];
  $("cards").innerHTML = lista.map(([v, r, s]) => `<div class="card"><b>${num(v)}</b><span>${r}</span><small>${s}</small></div>`).join("");
}

function talhoes(lista, recomendacoes, limites) {
  const rec = new Map();
  recomendacoes.forEach((a) => { const k = `${a.propriedade}|${a.talhao}`; if (!rec.has(k)) rec.set(k, a); });
  $("talhoes").innerHTML = lista.map((t) => {
    const r = rec.get(`${t.propriedade}|${t.talhao}`);
    const seco = t.umidade_solo_pct < limites.r3_liga_abaixo_pct;
    const pragas = t.pragas_armadilha >= limites.r5_nivel_atencao;
    const umido = t.umidade_ar_pct >= limites.r4_ur_min_pct;
    return `<article class="talhao">
      <header><div><h3>${esc(t.propriedade)} · ${esc(t.talhao)}</h3>
        <small>${esc(t.cultura)} · ${esc(t.municipio)}/${esc(t.uf)} · leitura de ${quando(t.instante)}</small></div>
        <span class="irrig ${t.irrigacao_ligada ? "on" : ""}">${t.irrigacao_ligada ? "IRRIGANDO" : "irrigação desligada"}</span></header>
      <div class="medidas">
        <div class="${seco ? "fora" : ""}"><b>${num(t.umidade_solo_pct, 1)}%</b><span>solo</span></div>
        <div><b>${num(t.temperatura_c, 1)}°</b><span>temp.</span></div>
        <div class="${umido ? "fora" : ""}"><b>${num(t.umidade_ar_pct)}%</b><span>UR ar</span></div>
        <div><b>${num(t.chuva_mm, 1)}</b><span>chuva mm</span></div>
        <div class="${pragas ? "fora" : ""}"><b>${num(t.pragas_armadilha)}</b><span>pragas</span></div>
      </div>
      ${r && r.severidade === "informativa" ? `<p class="recomenda ok">Situação normalizada (${esc(r.regra)}): ${esc(r.mensagem)}</p>`
        : r ? `<p class="recomenda"><strong>${esc(r.regra)} · ${esc(SEV[r.severidade][0])}:</strong> ${esc(r.recomendacao)}</p>`
          : `<p class="recomenda ok">Nenhuma ação recomendada para este talhão.</p>`}
    </article>`;
  }).join("") || `<p class="recomenda ok">Ainda sem leituras de sensores.</p>`;
}

function alertas(lista) {
  $("n-alertas").textContent = lista.length ? `(${lista.length} mais recentes)` : "";
  $("alertas").innerHTML = lista.map((a) => {
    const [rotulo, cor] = SEV[a.severidade];
    const novo = !primeiraCarga && !vistos.has(a.id);
    const botoes = a.acoes.map((x) => `<button type="button" class="${x.status}" data-acao="${x.id}"
        title="${esc(x.detalhe)}">${ACAO[x.tipo] || x.tipo}${x.status === "executada" ? "" : ` (${x.status})`}</button>`).join("");
    return `<article class="alerta ${novo ? "novo" : ""}" style="--cor:${cor}">
      <div class="topo"><span class="selo">${rotulo}</span><span class="regra">${esc(a.regra)}</span>
        <span>dado de ${quando(a.instante_evento)}</span><span>· alerta nº ${a.id} · lote ${a.id_lote}</span></div>
      <h3>${esc(a.titulo)}</h3>
      <p>${esc(a.mensagem)}</p>
      <p class="rec"><strong>Ação recomendada:</strong> ${esc(a.recomendacao)}</p>
      <div class="acoes">${botoes}</div>
    </article>`;
  }).join("") || `<p class="recomenda ok">Nenhum alerta até agora — as regras ainda não encontraram condição de ação.</p>`;
  lista.forEach((a) => vistos.add(a.id));
}

function tabela(id, cabecalho, linhas) {
  $(id).innerHTML = `<thead><tr>${cabecalho.map((c) => `<th>${c}</th>`).join("")}</tr></thead>
    <tbody>${linhas.join("") || `<tr><td colspan="${cabecalho.length}" class="zero">—</td></tr>`}</tbody>`;
}
const n = (v) => `<td class="n ${v ? "" : "zero"}">${num(v)}</td>`;

function render(d) {
  monitor(d.monitor);
  $("entrada").textContent = d.entrada;
  $("vazio").hidden = d.disponivel;
  $("conteudo").hidden = !d.disponivel;
  if (!d.disponivel) { $("vazio-motivo").textContent = d.motivo; return; }

  cards(d.totais);
  talhoes(d.talhoes, d.recomendacoes, d.limites);
  alertas(d.alertas);
  tabela("lotes", ["Lote", "Concluído", "Novos", "Já proc.", "Rejeit.", "Alertas", "Ações"],
    d.lotes.map((l) => `<tr title="${esc(JSON.parse(l.arquivos || "[]").join(", "))}"><td>#${l.id}</td>
      <td>${quando(l.concluido_em)}</td>${n(l.novos)}${n(l.duplicados)}${n(l.rejeitados)}${n(l.alertas)}${n(l.acoes)}</tr>`));
  tabela("arquivos", ["Arquivo", "Tipo", "Linhas lidas", "Bytes"],
    d.arquivos.map((a) => `<tr><td>${esc(a.nome)}</td><td>${esc(a.tipo)}</td>${n(a.linhas_lidas)}${n(a.bytes_lidos)}</tr>`));
  tabela("rejeitados", ["Arquivo", "Linha", "Motivo"],
    d.rejeitados.map((r) => `<tr><td>${esc(r.arquivo)}</td>${n(r.linha)}<td>${esc(r.motivos)}</td></tr>`));
  tabela("regras", ["Regra", "SE (lógica booleana)", "ENTÃO", "Latência", "Disparos"],
    d.regras.map((r) => `<tr><td><b>${r.id}</b><br>${esc(r.nome)}</td><td><code>${esc(r.expressao)}</code></td>
      <td>${esc(r.acoes)}</td><td>${esc(r.latencia)}</td>${n(d.por_regra[r.id] || 0)}</tr>`));
  primeiraCarga = false;
}

async function atualizar() {
  try {
    const resposta = await fetch("/api/automacao", { cache: "no-store" });
    if (resposta.ok) render(await resposta.json());
  } catch (erro) {
    $("monitor").querySelector("span").textContent = "servidor indisponível";
  } finally {
    setTimeout(atualizar, INTERVALO_MS);
  }
}

async function abrirAcao(id) {
  const resposta = await fetch(`/api/automacao/acao/${id}`);
  if (!resposta.ok) return;
  const c = await resposta.json();
  $("visor-titulo").textContent = ACAO[c.tipo] || c.tipo;
  $("visor-arquivo").textContent = `dados/fase3/${c.arquivo}`;
  $("visor-texto").textContent = c.texto;
  $("visor").showModal();
}
document.addEventListener("click", (evento) => {
  const botao = evento.target.closest("button[data-acao]");
  if (botao) abrirAcao(botao.dataset.acao);
});
// /automacao?acao=ID abre direto o arquivo gerado por uma ação (útil para mostrar no vídeo).
const acaoNaUrl = new URLSearchParams(location.search).get("acao");
if (acaoNaUrl) abrirAcao(acaoNaUrl);
$("visor-fechar").addEventListener("click", () => $("visor").close());

atualizar();
