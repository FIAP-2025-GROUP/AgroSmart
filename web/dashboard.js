/* AgroSmart — painel analítico (Fase 2). Sem dependências, sem build.
   Tudo vem de GET /api/analitica, que lê o pacote processado pelo pipeline. */

const $ = (id) => document.getElementById(id);

const NI = "__nao_informado__";
const CORES = {
  saudavel: "var(--c-saudavel)",
  doente: "var(--c-doente)",
  indeterminado: "var(--c-indeterminado)",
  serie: "var(--c-serie)",
};
// Doente embaixo: a tendência que importa fica apoiada na linha de base.
const ORDEM_CONDICAO = ["doente", "indeterminado", "saudavel"];

const ROTULOS = {
  condicao: { saudavel: "Saudável", doente: "Doente", indeterminado: "Indeterminado" },
  categoria: {
    saudavel: "Saudável", doenca: "Doença", praga: "Praga",
    outra_anomalia: "Outra anomalia", indeterminado: "Indeterminado",
  },
  motor: { cnn: "CNN especializado", vlm: "Gemini (VLM)" },
  origem: { real: "Real", simulado: "Simulado", todas: "Real + simulado" },
};

const estado = { origem: null, filtros: {}, pagina: 1 };
// Análise recém-enviada pela tela principal ("Ver no painel"): confirmada uma vez.
let destaque = null;
let ultimo = null;

/* ------------------------------------------------------------- utilidades */
const escapar = (texto) =>
  String(texto ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const numero = (n) => (n ?? 0).toLocaleString("pt-BR");
const pct = (p) =>
  p == null ? null : `${(p * 100).toLocaleString("pt-BR", { maximumFractionDigits: 1, minimumFractionDigits: 1 })}%`;

function rotulo(campo, valor) {
  if (valor == null || valor === NI) return "Não informado";
  return ROTULOS[campo]?.[valor] ?? valor;
}

function dataBR(iso, comHora = false) {
  if (!iso) return "—";
  const d = new Date(iso.length === 10 ? `${iso}T12:00:00` : iso);
  if (Number.isNaN(d.getTime())) return iso;
  return comHora
    ? d.toLocaleString("pt-BR", { dateStyle: "short", timeStyle: "short" })
    : d.toLocaleDateString("pt-BR");
}

const semanaCurta = (iso) => `${iso.slice(8, 10)}/${iso.slice(5, 7)}`;

function avisar(mensagem) {
  const caixa = $("aviso");
  caixa.textContent = mensagem;
  caixa.hidden = false;
  clearTimeout(avisar.timer);
  avisar.timer = setTimeout(() => (caixa.hidden = true), 7000);
}

const vazio = (texto = "Sem dados para os filtros escolhidos.") =>
  `<div class="vazio-grafico">${escapar(texto)}</div>`;

const legenda = (itens) =>
  `<div class="legenda">${itens.map(([nome, cor]) =>
    `<span><i style="background:${cor}"></i>${escapar(nome)}</span>`).join("")}</div>`;

/* ------------------------------------------------------------ dica (hover) */
const dica = $("dica");
document.addEventListener("mousemove", (e) => {
  const alvo = e.target.closest("[data-dica]");
  if (!alvo) { dica.hidden = true; return; }
  dica.innerHTML = alvo.dataset.dica;
  dica.hidden = false;
  const margem = 14;
  const { width, height } = dica.getBoundingClientRect();
  let x = e.clientX + margem;
  let y = e.clientY + margem;
  if (x + width > innerWidth - 8) x = e.clientX - width - margem;
  if (y + height > innerHeight - 8) y = e.clientY - height - margem;
  dica.style.left = `${x}px`;
  dica.style.top = `${y}px`;
});

const linhaDica = (cor, texto) =>
  `<span class="linha-dica"><i style="background:${cor}"></i>${texto}</span>`;

/* --------------------------------------------------------------- consulta */
function lerUrl() {
  const params = new URLSearchParams(location.search);
  destaque = params.get("analise");
  estado.origem = params.get("origem");
  estado.pagina = Number(params.get("pagina")) || 1;
  estado.filtros = {};
  for (const campo of ["inicio", "fim", "uf", "municipio", "propriedade", "talhao",
    "cultura", "condicao", "categoria", "motor"]) {
    if (params.get(campo)) estado.filtros[campo] = params.get(campo);
  }
}

function montarConsulta() {
  const params = new URLSearchParams();
  if (estado.origem) params.set("origem", estado.origem);
  Object.entries(estado.filtros).forEach(([k, v]) => v && params.set(k, v));
  if (estado.pagina > 1) params.set("pagina", estado.pagina);
  return params.toString();
}

/* Só a consulta mais recente pode pintar a tela. Cada chamada cancela a anterior
   (AbortController) e carrega um número de geração: uma resposta que chegue
   atrasada, de filtros que já não estão na tela, é descartada. */
let geracao = 0;
let controlador = null;

function mostrarErro(mensagem) {
  // Nada de deixar números antigos à vista como se fossem do recorte atual.
  $("resultados").hidden = true;
  $("faixas").innerHTML = "";
  $("erro-mensagem").textContent = mensagem;
  $("erro-consulta").hidden = false;
}

function mensagemDeErro(dados, status) {
  const detalhe = dados?.detail;
  if (typeof detalhe === "string") return detalhe;
  if (Array.isArray(detalhe)) return detalhe.map((d) => d.msg || JSON.stringify(d)).join("; ");
  return `Erro ${status} ao consultar o painel.`;
}

async function carregar() {
  const minha = ++geracao;
  controlador?.abort();
  controlador = new AbortController();

  const consulta = montarConsulta();
  history.replaceState(null, "", consulta ? `?${consulta}` : location.pathname);
  $("resultados").classList.add("carregando");
  $("resultados").setAttribute("aria-busy", "true");

  let dados;
  try {
    const resposta = await fetch(`/api/analitica${consulta ? `?${consulta}` : ""}`,
      { signal: controlador.signal });
    dados = await resposta.json().catch(() => null);
    if (!resposta.ok) throw new Error(mensagemDeErro(dados, resposta.status));
    if (!dados) throw new Error("Resposta ilegível do servidor.");
  } catch (erro) {
    if (erro.name === "AbortError" || minha !== geracao) return; // superada por outra consulta
    $("resultados").classList.remove("carregando");
    $("resultados").removeAttribute("aria-busy");
    mostrarErro(erro.message || "Não foi possível falar com o servidor.");
    return;
  }
  if (minha !== geracao) return;

  ultimo = dados;
  $("resultados").classList.remove("carregando");
  $("resultados").removeAttribute("aria-busy");
  $("erro-consulta").hidden = true;

  if (!dados.disponivel) {
    $("conteudo").hidden = true;
    $("faixas").innerHTML = "";
    const caixa = $("sem-pacote");
    caixa.hidden = false;
    caixa.innerHTML = `<h2>Nenhum dado processado disponível</h2>
      <p>${escapar(dados.mensagem)}</p>
      ${dados.aviso ? `<p>${escapar(dados.aviso)}</p>` : ""}
      <p><code>.venv\\Scripts\\python.exe ferramentas\\executar_pipeline.py</code></p>`;
    return;
  }

  estado.origem = dados.origem;
  $("sem-pacote").hidden = true;
  $("conteudo").hidden = false;
  $("resultados").hidden = false;
  renderizarOrigens(dados);
  renderizarFaixas(dados);
  renderizarFiltros(dados);
  renderizarCards(dados.cards);
  renderizarEvolucao(dados.graficos.evolucao);
  renderizarCondicao(dados.graficos.condicao, dados.cards.analises);
  renderizarCategorias(dados.graficos.categorias, dados.cards.analises);
  renderizarDiagnosticos(dados.graficos.diagnosticos);
  renderizarLocalidades(dados.graficos.localidades);
  renderizarCulturas(dados.graficos.culturas);
  renderizarMotores(dados.graficos);
  renderizarQualidade(dados.qualidade);
  renderizarTabela(dados.tabela);
  confirmarDestaque(dados);
}

$("tentar-de-novo").addEventListener("click", () => carregar());
// Um filtro inválido vindo da URL impede até a barra de filtros de aparecer:
// este botão é a saída sem precisar editar o endereço.
$("erro-limpar").addEventListener("click", () => {
  estado.filtros = {};
  estado.pagina = 1;
  carregar();
});

/* --------------------------------------------------------- origem e faixas */
function renderizarOrigens(dados) {
  for (const botao of $("origens").querySelectorAll("button")) {
    const origem = botao.dataset.origem;
    botao.setAttribute("aria-pressed", String(origem === dados.origem));
    const contagem = dados.origens[origem];
    if (contagem) {
      $(`n-${origem}`).textContent = numero(contagem.analises || contagem.laudos);
      botao.disabled = !contagem.analises && !contagem.laudos;
      botao.title = `${numero(contagem.analises)} análises · ${numero(contagem.laudos)} laudos`;
    }
  }
}

function horaBR(iso) {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "" : d.toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" });
}

function renderizarFaixas(dados) {
  const p = dados.pacote;
  const vivo = `Reais: lidos ao vivo do histórico local às ${horaBR(dados.fonte_real.lida_em)}`;
  const pacote = p.id_execucao
    ? `Simulados: pacote processado em ${dataBR(p.processado_em, true)} · execução <code>${escapar(p.id_execucao)}</code>`
      + ` · ${escapar(p.ambiente || "")}`
    : "Simulados: nenhum pacote processado disponível";
  const atualizado = { real: vivo, simulado: pacote, todas: `${vivo} · ${pacote}` }[dados.origem];

  const textos = {
    simulado: `<span class="etiqueta etiqueta-simulado">Dados simulados</span>
      <span><strong>Demonstração.</strong> Registros sintéticos, gerados com semente fixa —
      não representam incidência real de doenças em nenhuma propriedade.</span>`,
    real: `<span class="etiqueta etiqueta-real">Dados reais · ao vivo</span>
      <span>Análises feitas na tela principal do AgroSmart, lidas direto do histórico local — aparecem
      aqui assim que são salvas, sem esperar o pipeline Spark.</span>`,
    todas: `<span class="etiqueta etiqueta-todas">Real + simulado</span>
      <span>Visão combinada para conferência. <strong>Não use para conclusões:</strong> mistura
      registros reais com sintéticos.</span>`,
  };

  const legado = dados.origem !== "simulado" && dados.cards.laudos_sem_analise
    ? `<div class="faixa faixa-todas">${numero(dados.cards.laudos_sem_analise)} laudo(s) real(is) sem
       identificador de análise — gravados antes do registro de análises, ou por outro caminho (como o app
       Streamlit). Eles entram na contagem de laudos, na distribuição por motor e na tabela, mas não nos
       indicadores por análise: agrupá-los seria inventar números.</div>`
    : "";

  const reais = dados.origens.real?.analises || 0;
  const regra = dados.origem === "simulado" && reais && reais < dados.minimo_reais_padrao
    ? `<div class="faixa faixa-todas">Há ${numero(reais)} análise(s) real(is) — veja em <strong>Reais</strong>.
       O painel abre em Simulados até existirem ${dados.minimo_reais_padrao} análises reais.</div>`
    : "";

  // Nenhum registro em nenhuma origem (ex.: RAW vazio e histórico vazio).
  const semRegistros = Object.values(dados.origens).every((o) => !o.analises && !o.laudos);
  const texto = semRegistros
    ? `<span class="etiqueta etiqueta-todas">Sem registros</span>
       <span>Não há nenhum laudo, real ou simulado, para mostrar.</span>`
    : textos[dados.origem];
  $("faixas").innerHTML =
    `<div class="faixa faixa-${semRegistros ? "todas" : dados.origem}">${texto}<span class="meta">${atualizado}</span></div>`
    + legado + regra
    + (p.aviso ? `<div class="faixa faixa-aviso">${escapar(p.aviso)}</div>` : "");
}

/** Confirma que a análise enviada agora (link "Ver no painel") entrou no painel. */
function confirmarDestaque(dados) {
  if (!destaque) return;
  const id = destaque;
  destaque = null; // só na primeira carga
  const laudos = dados.tabela.itens.filter((x) => x.id_analise === id);
  const caixa = document.createElement("div");
  if (laudos.length) {
    const principal = laudos[0];
    caixa.className = "faixa faixa-confirmacao";
    caixa.innerHTML = `<span class="etiqueta etiqueta-real">✓ Análise incluída</span>
      <span>A foto enviada agora já está no painel: <strong>${escapar(rotulo("condicao", principal.condicao))}</strong>
      · ${escapar(principal.diagnostico_normalizado ?? "—")} · ${laudos.length} laudo(s). Ela aparece no topo da
      tabela de laudos e já conta nos indicadores de Reais.</span>`;
  } else {
    caixa.className = "faixa faixa-aviso";
    caixa.textContent = "A análise enviada não aparece nesta visão. Confira se a origem é Reais e se "
      + "nenhum filtro a está escondendo.";
  }
  $("faixas").prepend(caixa);
}

/* ----------------------------------------------------------------- filtros */
function preencherSelect(nome, valores, campoRotulo) {
  const select = $("filtros").elements[nome];
  const atual = estado.filtros[nome] || "";
  const lista = [...valores];
  if (atual && !lista.includes(atual)) lista.push(atual); // não some com a escolha feita
  select.innerHTML = `<option value="">Todos</option>` + lista.map((v) =>
    `<option value="${escapar(v)}">${escapar(rotulo(campoRotulo, v))}</option>`).join("");
  select.value = atual;
}

function renderizarFiltros(dados) {
  const o = dados.opcoes;
  ["uf", "municipio", "propriedade", "talhao", "cultura"].forEach((c) => preencherSelect(c, o[c], c));
  preencherSelect("condicao", o.condicao, "condicao");
  preencherSelect("categoria", o.categoria, "categoria");
  preencherSelect("motor", o.motor, "motor");
  const form = $("filtros");
  for (const campo of ["inicio", "fim"]) {
    form.elements[campo].value = estado.filtros[campo] || "";
    form.elements[campo].min = o.periodo.min || "";
    form.elements[campo].max = o.periodo.max || "";
  }
  form.elements.inicio.placeholder = o.periodo.min || "";
}

$("filtros").addEventListener("change", (e) => {
  const { name, value } = e.target;
  if (!name) return;
  estado.filtros[name] = value;
  // Localidade é hierárquica: trocar a UF invalida município, propriedade e talhão.
  const abaixo = { uf: ["municipio", "propriedade", "talhao"], municipio: ["propriedade", "talhao"], propriedade: ["talhao"] };
  (abaixo[name] || []).forEach((c) => delete estado.filtros[c]);
  estado.pagina = 1;
  carregar();
});

$("limpar-filtros").addEventListener("click", () => {
  estado.filtros = {};
  estado.pagina = 1;
  carregar();
});

$("origens").addEventListener("click", (e) => {
  const botao = e.target.closest("button[data-origem]");
  if (!botao || botao.disabled) return;
  estado.origem = botao.dataset.origem;
  estado.filtros = {}; // as opções de localidade mudam com a origem
  estado.pagina = 1;
  carregar();
});

/* ------------------------------------------------------------------- cards */
function card(titulo, valor, detalhe = "", cor = null) {
  const marca = cor ? `<i style="background:${cor}"></i>` : "";
  const corpo = valor == null ? `<span class="sem-dados">sem dados</span>` : valor;
  return `<div class="card vidro"><dt>${marca}${escapar(titulo)}</dt><dd>${corpo}</dd>
    ${detalhe ? `<small>${detalhe}</small>` : ""}</div>`;
}

function cardPct(titulo, razao, cor) {
  return card(titulo, pct(razao.pct),
    razao.den ? `${numero(razao.num)} de ${numero(razao.den)} análises` : "nenhuma análise identificada", cor);
}

function renderizarCards(c) {
  $("cards").innerHTML = [
    card("Análises identificadas", c.analises ? numero(c.analises) : null,
      c.laudos_sem_analise ? `+ ${numero(c.laudos_sem_analise)} laudo(s) sem id de análise` : "uma execução sobre uma imagem"),
    card("Laudos", c.laudos ? numero(c.laudos) : null, "resultados emitidos por CNN ou VLM"),
    card("Imagens distintas", c.imagens_distintas == null ? null : numero(c.imagens_distintas),
      c.analises_sem_id_imagem ? `${numero(c.analises_sem_id_imagem)} análise(s) sem id de imagem, fora da conta`
        : (c.analises ? "por id_imagem" : "")),
    cardPct("Saudável", c.saudavel, CORES.saudavel),
    cardPct("Doente", c.doente, CORES.doente),
    cardPct("Indeterminado", c.indeterminado, CORES.indeterminado),
  ].join("");

  const notas = [];
  if (c.reanalises) notas.push(`<span><b>${numero(c.reanalises)}</b> reanálise(s) de imagem já analisada</span>`);
  if (c.comparacoes) {
    notas.push(`<span><b>${numero(c.comparacoes)}</b> análise(s) no modo comparação — os motores divergiram em
      <b>${numero(c.discordancias)}</b> de ${numero(c.comparacoes_com_veredito)} com veredito dos dois</span>`);
  }
  $("notas").innerHTML = notas.join("");
}

/* -------------------------------------------------------- barras (HTML) */
function barras(itens, maximo) {
  if (!itens.length) return vazio();
  const topo = maximo || Math.max(...itens.map((i) => i.total), 1);
  return `<div class="barras">${itens.map((item) => {
    const segmentos = item.segmentos.filter((s) => s.valor > 0).map((s) =>
      `<span style="width:${(s.valor / topo) * 100}%;background:${s.cor}"></span>`).join("");
    return `<div class="barra" data-dica="${escapar(item.dica)}">
      <span class="barra-rotulo" title="${escapar(item.rotulo)}">${escapar(item.rotulo)}</span>
      <span class="barra-trilho">${segmentos}</span>
      <span class="barra-valor">${item.valorTexto}</span>
    </div>`;
  }).join("")}</div>`;
}

function renderizarCondicao(contagem, total) {
  const porChave = Object.fromEntries(contagem.map((x) => [x.chave, x.n]));
  const itens = ["saudavel", "doente", "indeterminado"].filter((k) => porChave[k] != null).map((k) => ({
    rotulo: rotulo("condicao", k),
    total: porChave[k],
    segmentos: [{ valor: porChave[k], cor: CORES[k] }],
    valorTexto: `${numero(porChave[k])}<small>${pct(porChave[k] / total)}</small>`,
    dica: linhaDica(CORES[k], `<b>${rotulo("condicao", k)}</b>: ${numero(porChave[k])} de ${numero(total)} análises (${pct(porChave[k] / total)})`),
  }));
  $("g-condicao").innerHTML = total ? barras(itens) : vazio();
}

function renderizarCategorias(contagem, total) {
  const itens = contagem.map((x) => ({
    rotulo: rotulo("categoria", x.chave),
    total: x.n,
    segmentos: [{ valor: x.n, cor: CORES.serie }],
    valorTexto: `${numero(x.n)}<small>${pct(x.n / total)}</small>`,
    dica: `<b>${escapar(rotulo("categoria", x.chave))}</b><br>${numero(x.n)} de ${numero(total)} análises (${pct(x.n / total)})`,
  }));
  $("g-categorias").innerHTML = total ? barras(itens) : vazio();
}

function renderizarDiagnosticos(lista) {
  const itens = lista.map((x) => ({
    rotulo: x.chave ?? "Não informado",
    total: x.n,
    segmentos: [{ valor: x.n, cor: CORES.serie }],
    valorTexto: `${numero(x.n)}<small>${pct(x.n / x.den)}</small>`,
    dica: `<b>${escapar(x.chave ?? "Não informado")}</b> · ${escapar(rotulo("categoria", x.categoria))}<br>
      ${numero(x.n)} de ${numero(x.den)} análises doentes (${pct(x.n / x.den)})`,
  }));
  $("g-diagnosticos").innerHTML = itens.length ? barras(itens) : vazio("Nenhuma análise doente no recorte.");
}

function renderizarLocalidades(lista) {
  const nome = (l) => (l.municipio ? `${l.municipio} · ${l.uf}` : "Localidade incompleta");
  const itens = lista.map((l) => ({
    rotulo: nome(l),
    total: l.doente.pct ?? 0,
    segmentos: [{ valor: l.doente.pct ?? 0, cor: CORES.doente }],
    valorTexto: `${pct(l.doente.pct)}<small>${numero(l.doente.num)}/${numero(l.doente.den)}</small>`,
    dica: `<b>${escapar(nome(l))}</b><br>${linhaDica(CORES.doente, `Doentes: ${numero(l.n_doente)}`)}
      ${linhaDica(CORES.saudavel, `Saudáveis: ${numero(l.n_saudavel)}`)}
      ${linhaDica(CORES.indeterminado, `Indeterminadas: ${numero(l.n_indeterminado)}`)}
      ${numero(l.n_analises)} análises · ${pct(l.doente.pct)} doentes`,
  }));
  $("g-localidades").innerHTML = itens.length ? barras(itens, 1) : vazio();
}

function segmentosCondicao(linha) {
  return ORDEM_CONDICAO.map((k) => ({ valor: linha[`n_${k}`], cor: CORES[k] }));
}

function dicaCondicao(titulo, linha) {
  return `<b>${escapar(titulo)}</b> · ${numero(linha.n_analises)} análises<br>`
    + ORDEM_CONDICAO.map((k) => linhaDica(CORES[k],
      `${rotulo("condicao", k)}: ${numero(linha[`n_${k}`])} (${pct(linha[`n_${k}`] / linha.n_analises)})`)).join("");
}

const legendaCondicao = () =>
  legenda(ORDEM_CONDICAO.map((k) => [rotulo("condicao", k), CORES[k]]));

function renderizarCulturas(lista) {
  const itens = lista.map((l) => ({
    rotulo: l.cultura ?? "Não identificada",
    total: l.n_analises,
    segmentos: segmentosCondicao(l),
    valorTexto: `${numero(l.n_analises)}<small>${pct(l.n_doente / l.n_analises)} doentes</small>`,
    dica: dicaCondicao(l.cultura ?? "Não identificada", l),
  }));
  $("g-culturas").innerHTML = itens.length ? legendaCondicao() + barras(itens) : vazio();
}

function renderizarMotores(g) {
  const bloco = (lista, unidade) => {
    const total = lista.reduce((s, x) => s + x.n, 0);
    return barras(lista.map((x) => ({
      rotulo: rotulo("motor", x.chave),
      total: x.n,
      segmentos: [{ valor: x.n, cor: CORES.serie }],
      valorTexto: `${numero(x.n)}<small>${pct(x.n / total)}</small>`,
      dica: `<b>${escapar(rotulo("motor", x.chave))}</b><br>${numero(x.n)} de ${numero(total)} ${unidade} (${pct(x.n / total)})`,
    })));
  };
  $("g-motores").innerHTML = g.motores_laudos.length
    ? `<p class="subtitulo-grafico">Laudos emitidos</p>${bloco(g.motores_laudos, "laudos")}
       <p class="subtitulo-grafico">Motor do laudo principal da análise</p>${
         g.motores_analises.length ? bloco(g.motores_analises, "análises") : vazio("Sem análises identificadas.")}`
    : vazio();
}

/* -------------------------------------------------------- evolução (SVG) */
/** Teto e passo "redondos" (1, 2, 2,5 ou 5 × 10ⁿ) com no máximo 5 divisões. */
function escalaBonita(maximo) {
  if (maximo <= 0) return { teto: 1, passo: 1 };
  const potencia = 10 ** Math.floor(Math.log10(maximo));
  const passo = [0.1, 0.2, 0.25, 0.5, 1, 2, 2.5, 5, 10].map((m) => m * potencia).find((p) => maximo / p <= 5);
  return { teto: Math.ceil(maximo / passo) * passo, passo };
}

function renderizarEvolucao(semanas) {
  const alvo = $("g-evolucao");
  if (!semanas.length) { alvo.innerHTML = vazio(); return; }

  const L = 900, esq = 44, dir = 12, larguraUtil = L - esq - dir;
  const passo = larguraUtil / semanas.length;
  const largBarra = Math.min(46, passo * 0.62);
  const xCentro = (i) => esq + passo * i + passo / 2;
  const mostrarRotulo = (i) => semanas.length <= 16 || i % 2 === 0;

  // Colunas empilhadas (contagens)
  const H1 = 230, topo1 = 10, base1 = H1 - 24;
  const escala1 = escalaBonita(Math.max(...semanas.map((s) => s.n_analises)));
  const max1 = escala1.teto;
  const y1 = (v) => base1 - (v / max1) * (base1 - topo1);
  let svg1 = "";
  for (let v = 0; v <= max1 + 1e-9; v += escala1.passo) {
    svg1 += `<line class="linha-grade" x1="${esq}" x2="${L - dir}" y1="${y1(v)}" y2="${y1(v)}"/>
      <text x="${esq - 8}" y="${y1(v) + 4}" text-anchor="end">${numero(Math.round(v))}</text>`;
  }
  semanas.forEach((s, i) => {
    let acumulado = 0;
    const x = xCentro(i) - largBarra / 2;
    const presentes = ORDEM_CONDICAO.filter((k) => s[`n_${k}`] > 0);
    presentes.forEach((k, j) => {
      const v = s[`n_${k}`];
      const yTopo = y1(acumulado + v);
      const altura = y1(acumulado) - yTopo - (j < presentes.length - 1 ? 0 : 0);
      // 2px de respiro entre segmentos; o último ganha cantos arredondados.
      const gap = j > 0 ? 2 : 0;
      svg1 += `<rect x="${x}" y="${yTopo}" width="${largBarra}" height="${Math.max(0, altura - gap)}"
        rx="${j === presentes.length - 1 ? 3 : 0}" fill="${CORES[k]}"/>`;
      acumulado += v;
    });
    if (mostrarRotulo(i)) svg1 += `<text x="${xCentro(i)}" y="${H1 - 6}" text-anchor="middle">${semanaCurta(s.semana)}</text>`;
    svg1 += `<rect class="alvo" x="${esq + passo * i}" y="${topo1}" width="${passo}" height="${base1 - topo1}"
      data-dica="${escapar(dicaCondicao(`Semana de ${dataBR(s.semana)}`, s))}"/>`;
  });

  // Linha: parcela doente por semana (num/den da própria semana)
  const H2 = 150, topo2 = 12, base2 = H2 - 24;
  const pcts = semanas.map((s) => s.pct_doente ?? 0);
  const escala2 = escalaBonita(Math.max(...pcts, 0.01) * 100);
  const max2 = Math.min(1, escala2.teto / 100);
  const y2 = (v) => base2 - (v / max2) * (base2 - topo2);
  let svg2 = "";
  for (let v = 0; v <= max2 + 1e-9; v += Math.max(escala2.passo / 100, max2 / 2)) {
    svg2 += `<line class="linha-grade" x1="${esq}" x2="${L - dir}" y1="${y2(v)}" y2="${y2(v)}"/>
      <text x="${esq - 8}" y="${y2(v) + 4}" text-anchor="end">${Math.round(v * 100)}%</text>`;
  }
  const pontos = semanas.map((s, i) => [xCentro(i), y2(s.pct_doente ?? 0)]);
  svg2 += `<polyline points="${pontos.map((p) => p.join(",")).join(" ")}" fill="none"
    stroke="${CORES.doente}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>`;
  pontos.forEach(([x, y], i) => {
    svg2 += `<circle cx="${x}" cy="${y}" r="4" fill="${CORES.doente}" stroke="#0b2716" stroke-width="2"/>`;
    if (mostrarRotulo(i)) svg2 += `<text x="${x}" y="${H2 - 6}" text-anchor="middle">${semanaCurta(semanas[i].semana)}</text>`;
  });
  // Rótulo direto só no último ponto e no pico — não em todos.
  const pico = pcts.indexOf(Math.max(...pcts));
  new Set([pico, pcts.length - 1]).forEach((i) => {
    svg2 += `<text x="${pontos[i][0]}" y="${pontos[i][1] - 10}" text-anchor="middle"
      style="fill:var(--texto);font-weight:600">${pct(pcts[i])}</text>`;
  });
  semanas.forEach((s, i) => {
    svg2 += `<rect class="alvo" x="${esq + passo * i}" y="${topo2}" width="${passo}" height="${base2 - topo2}"
      data-dica="${escapar(`<b>Semana de ${dataBR(s.semana)}</b><br>${linhaDica(CORES.doente,
        `${pct(s.pct_doente)} doentes — ${numero(s.n_doente)} de ${numero(s.n_analises)} análises`)}`)}"/>`;
  });

  alvo.innerHTML = legendaCondicao()
    + `<div class="rolagem-svg"><svg class="svg-grafico" viewBox="0 0 ${L} ${H1}" role="img" aria-label="Análises por semana e condição">${svg1}</svg></div>
       <p class="subtitulo-grafico">Parcela de análises doentes por semana</p>
       <div class="rolagem-svg"><svg class="svg-grafico" viewBox="0 0 ${L} ${H2}" role="img" aria-label="Percentual de análises doentes por semana">${svg2}</svg></div>`;
}

/* --------------------------------------------------------------- qualidade */
function indicador(titulo, razao, detalhe, medidor = true) {
  const valor = razao.pct == null ? "sem dados" : pct(razao.pct);
  return `<div class="indicador"><span>${escapar(titulo)}</span><b>${valor}</b>
    <small>${detalhe}</small>
    ${medidor && razao.pct != null ? `<div class="medidor-q"><span style="width:${razao.pct * 100}%"></span></div>` : ""}
  </div>`;
}

function renderizarQualidade(q) {
  const p = q.pacote;
  const r = q.recorte;
  const pr = (nome) => p[nome] ? { num: p[nome].valor, den: p[nome].denominador, pct: p[nome].pct } : { num: 0, den: 0, pct: null };
  const motivos = Object.entries(p).filter(([k]) => k.startsWith("rejeitado:"))
    .map(([k, v]) => `<li><code>${escapar(k.slice(10))}</code> ${numero(v.valor)}</li>`).join("");
  const rejeitados = pr("rejeitados");
  const recebidos = p.registros_recebidos?.valor ?? 0;

  const amostra = q.rejeitados_amostra.length
    ? `<details class="rejeitados"><summary>Ver exemplos de registros rejeitados</summary><ul>${
        q.rejeitados_amostra.map((x) => `<li><code>${escapar(x.motivo_rejeicao)}</code> ${escapar(x.id_laudo ?? "sem id_laudo")}</li>`).join("")
      }</ul></details>`
    : "";

  $("qualidade").innerHTML = `
    <h2>Qualidade e cobertura dos dados</h2>
    <p class="sub">Os três primeiros indicadores valem para o recorte filtrado; os demais, para toda a origem
       selecionada nesta execução do pipeline.</p>
    <div class="grade-qualidade">
      ${indicador("Análises com localidade completa", r.localidade_completa, `${numero(r.localidade_completa.num)} de ${numero(r.localidade_completa.den)} análises (UF e município)`)}
      ${indicador("Análises com id de imagem", r.com_id_imagem, `${numero(r.com_id_imagem.num)} de ${numero(r.com_id_imagem.den)} análises`)}
      ${indicador("Laudos sem id de análise", r.laudos_sem_analise, `${numero(r.laudos_sem_analise.num)} de ${numero(r.laudos_sem_analise.den)} laudos`, false)}
      ${indicador("Registros rejeitados na Silver", rejeitados.den ? rejeitados : { pct: recebidos ? 0 : null },
        `${numero(rejeitados.num)} de ${numero(recebidos)} recebidos${motivos ? `<ul style="margin:6px 0 0;padding-left:16px">${motivos}</ul>` : ""}`, false)}
      ${indicador("Identidade incompleta", pr("identidade_incompleta"), `${numero(pr("identidade_incompleta").num)} laudos sem id_analise ou id_imagem`, false)}
      ${indicador("Diagnóstico sem sinônimo conhecido", pr("diagnostico_nao_mapeado"), `${numero(pr("diagnostico_nao_mapeado").num)} laudos doentes mantidos com o texto original`, false)}
    </div>
    ${amostra}`;
}

/* ------------------------------------------------------------------ tabela */
function renderizarTabela(t) {
  $("registros-resumo").textContent = t.total
    ? `${numero(t.total)} laudo(s), do mais recente ao mais antigo — uma análise no modo comparação tem dois`
    : "Nenhum laudo no recorte.";
  $("pag-info").textContent = `${t.pagina} / ${t.paginas}`;
  $("pag-anterior").disabled = t.pagina <= 1;
  $("pag-proxima").disabled = t.pagina >= t.paginas;

  const ni = `<span class="ni">não informado</span>`;
  $("tabela").innerHTML = t.itens.map((r) => {
    const local = [r.municipio, r.uf].filter(Boolean).join(" · ");
    const sublocal = [r.propriedade, r.talhao].filter(Boolean).join(" · ");
    const confianca = r.confianca != null
      ? pct(r.confianca)
      : `<span class="ni">${escapar(r.confianca_texto || "—")}</span>`;
    const original = r.diagnostico_original && r.diagnostico_original !== r.diagnostico_normalizado
      ? `<span class="orig">original: ${escapar(r.diagnostico_original)}</span>` : "";
    return `<tr>
      <td class="num">${dataBR(r.data_analise_ts, true)}</td>
      <td>${local ? escapar(local) : ni}${sublocal ? `<span class="orig">${escapar(sublocal)}</span>` : ""}</td>
      <td>${r.cultura ? escapar(r.cultura) : ni}</td>
      <td><span class="ponto" style="--cor:${CORES[r.condicao]}">${escapar(rotulo("condicao", r.condicao))}</span></td>
      <td>${escapar(r.diagnostico_normalizado ?? "—")}${original}</td>
      <td>${escapar(rotulo("motor", r.motor))}</td>
      <td class="num">${confianca}</td>
      <td>${escapar(rotulo("origem", r.origem_dado))}</td>
    </tr>`;
  }).join("");
}

$("pag-anterior").addEventListener("click", () => { estado.pagina -= 1; carregar(); });
$("pag-proxima").addEventListener("click", () => { estado.pagina += 1; carregar(); });

/* ------------------------------------------------------------------ início */
lerUrl();
carregar();
