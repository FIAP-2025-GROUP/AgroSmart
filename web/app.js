/* AgroSmart — lógica do frontend. Sem dependências, sem build. */

const $ = (id) => document.getElementById(id);

const zona = $("zona");
const entrada = $("entrada");
const zonaVazia = $("zona-vazia");
const zonaPrevia = $("zona-previa");
const zonaCarregando = $("zona-carregando");
const previa = $("previa");
const previaNome = $("previa-nome");
const painelLaudo = $("laudo");
const grade = $("grade-historico");

let modoAtual = null;
let estado = null;
let analisando = false;
let urlPrevia = null; // objectURL da miniatura em tela — revogado a cada troca

const CONDICOES = {
  "saudável": { selo: "selo-saudavel", texto: "Saudável" },
  doente: { selo: "selo-doente", texto: "Doente" },
  indeterminado: { selo: "selo-indeterminado", texto: "Indeterminado" },
};

const escapar = (texto) =>
  String(texto ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

/* ------------------------------------------------------------------ avisos */
let timerAviso;
function avisar(mensagem, tipo = "erro") {
  const caixa = $("aviso");
  caixa.textContent = mensagem;
  caixa.className = "aviso-flutuante" + (tipo === "info" ? " info" : "");
  caixa.hidden = false;
  clearTimeout(timerAviso);
  timerAviso = setTimeout(() => (caixa.hidden = true), 7000);
}

/* ------------------------------------------------------------------ estado */
async function carregarEstado() {
  try {
    estado = await (await fetch("/api/estado")).json();
  } catch {
    avisar("Não foi possível falar com o servidor. Ele ainda está rodando?");
    return;
  }

  const cnn = $("estado-cnn");
  cnn.className = "pastilha " + (estado.cnn_disponivel ? "ok" : "off");
  cnn.title = estado.cnn_detalhe;

  const vlm = $("estado-vlm");
  vlm.className = "pastilha " + (estado.vlm_disponivel ? "ok" : "off");
  vlm.title = estado.vlm_disponivel
    ? `Chave carregada de: ${estado.vlm_origem_chave} · ${estado.vlm_modelo}`
    : "Sem chave. Preencha GEMINI_API_KEY no arquivo .env.";

  montarModos();
  montarClasses();
  $("num-total").textContent = estado.total_historico;
}

function montarModos() {
  const alvo = $("modos");
  alvo.innerHTML = "";
  modoAtual = estado.modo_padrao;

  estado.modos.forEach((modo) => {
    const botao = document.createElement("button");
    botao.type = "button";
    botao.className = "modo";
    botao.role = "radio";
    botao.textContent = modo;
    botao.setAttribute("aria-checked", String(modo === modoAtual));

    // Sem chave, os modos que dependem da IA visual ficam fora de alcance.
    const exigeVlm = modo !== "Somente CNN especializado";
    if (exigeVlm && !estado.vlm_disponivel) {
      botao.disabled = true;
      botao.title = "Requer a chave do Gemini no arquivo .env";
      if (modo === modoAtual) modoAtual = "Somente CNN especializado";
    }

    botao.addEventListener("click", () => {
      modoAtual = modo;
      [...alvo.children].forEach((b) =>
        b.setAttribute("aria-checked", String(b.textContent === modo)));
    });

    alvo.appendChild(botao);
  });

  [...alvo.children].forEach((b) =>
    b.setAttribute("aria-checked", String(b.textContent === modoAtual)));
}

function montarClasses() {
  $("grade-classes").innerHTML = estado.classes_cnn
    .map(
      (c) => `<div class="classe">
        <i class="${c.condicao === "saudável" ? "saudavel" : "doente"}"></i>
        <b>${escapar(c.cultura)}</b><span>${escapar(c.diagnostico)}</span>
      </div>`)
    .join("");
}

/* ------------------------------------------------------------------- envio */
/** Aponta o fundo desfocado da moldura para a mesma imagem exibida nela.
 *  Via CSSOM, não por atributo `style`, para a URL nunca virar markup. */
function pintarMoldura(elemento, fonte) {
  if (!elemento) return;
  if (fonte) elemento.style.setProperty("--foto", `url("${fonte}")`);
  else elemento.style.removeProperty("--foto");
}

function mostrar(qual) {
  zonaVazia.hidden = qual !== "vazia";
  zonaPrevia.hidden = qual !== "previa";
  zonaCarregando.hidden = qual !== "carregando";
}

/** Devolve a zona de envio ao estado inicial, preservando o laudo em tela. */
function resetarEnvio() {
  entrada.value = "";
  previa.removeAttribute("src");
  pintarMoldura($("moldura-previa"), null);
  previaNome.textContent = "";
  mostrar("vazia");

  // O objectURL só é descartado se o laudo em tela não estiver exibindo ele.
  if (urlPrevia && !painelLaudo.querySelector(`img[src="${urlPrevia}"]`)) {
    URL.revokeObjectURL(urlPrevia);
    urlPrevia = null;
  }
}

/** Zona ao estado inicial e laudo fora da tela. */
function limparZona({ rolar = false } = {}) {
  painelLaudo.hidden = true;
  painelLaudo.innerHTML = "";
  resetarEnvio();
  if (rolar) zona.scrollIntoView({ behavior: "smooth", block: "center" });
}

function receber(arquivo) {
  if (!arquivo) return;
  if (!/^image\/(png|jpe?g)$/i.test(arquivo.type)) {
    avisar("Formato não suportado. Envie PNG ou JPEG.");
    return;
  }
  if (arquivo.size > 20 * 1024 * 1024) {
    avisar("Imagem muito grande (máximo 20 MB).");
    return;
  }

  if (urlPrevia) URL.revokeObjectURL(urlPrevia);
  urlPrevia = URL.createObjectURL(arquivo);
  previa.src = urlPrevia;
  pintarMoldura($("moldura-previa"), urlPrevia);
  previaNome.textContent = arquivo.name;
  mostrar("previa");
  analisar(arquivo);
}

async function analisar(arquivo) {
  if (analisando) return;
  analisando = true;

  $("texto-carregando").textContent =
    modoAtual === "Somente CNN especializado"
      ? "Classificando a imagem…"
      : "Consultando a IA visual…";
  $("previa-scan").src = urlPrevia; // a própria foto sob a varredura
  pintarMoldura($("scanner"), urlPrevia);
  mostrar("carregando");
  painelLaudo.hidden = true;

  const corpo = new FormData();
  corpo.append("imagem", arquivo);
  corpo.append("modo", modoAtual);
  corpo.append("limiar", estado?.limiar_padrao ?? 0.6);
  corpo.append("salvar", "true");

  try {
    const resposta = await fetch("/api/analisar", { method: "POST", body: corpo });
    const dados = await resposta.json();

    if (!resposta.ok) throw new Error(dados.detail || `Erro ${resposta.status}`);

    (dados.avisos || []).forEach((a) => avisar(a, "info"));
    renderizarLaudos(dados.laudos, arquivo.name, urlPrevia);
    // A foto agora vive dentro do laudo: a zona volta a ficar livre para a
    // próxima planta, sem exigir nenhum clique de limpeza.
    resetarEnvio();
    await carregarHistorico();
  } catch (erro) {
    avisar(erro.message || "Falha ao analisar a imagem.");
    mostrar("previa"); // mantém a foto na zona para tentar de novo
  } finally {
    analisando = false;
  }
}

/* ------------------------------------------------------------------- laudo */
function blocoLaudo(laudo, figura = "") {
  const cond = CONDICOES[laudo.condicao] || CONDICOES.indeterminado;
  const pct = laudo.confianca != null ? Math.round(laudo.confianca * 100) : null;

  // Sem probabilidade calibrada (motor generalista) não há barra a preencher:
  // uma etiqueta compacta diz mais que um trilho vazio ocupando a largura toda.
  const medidor = pct !== null
    ? `<div class="medidor">
         <div class="medidor-trilho"><div class="medidor-barra" style="width:${pct}%"></div></div>
         <div class="medidor-legenda"><span>Confiança do classificador</span><span>${pct}%</span></div>
       </div>`
    : `<div class="medidor medidor-texto">
         <span>Confiança</span><b>${escapar(laudo.confianca_texto)}</b></div>`;

  const ranking = (laudo.ranking && laudo.ranking.length)
    ? `<details class="ranking"><summary>Outras hipóteses do classificador</summary><ol>${
        laudo.ranking.map((p) =>
          `<li>${escapar(p.rotulo)} — <code>${(p.confianca * 100).toFixed(1)}%</code></li>`).join("")
      }</ol></details>`
    : "";

  const detalhe = [laudo.especie, laudo.agente].filter((v) => v && v !== "—").join(" · ");

  return `
    <div class="laudo-topo">
      <div>
        <span class="selo ${cond.selo}">${cond.texto}</span>
        <h2>${escapar(laudo.diagnostico)}</h2>
        ${detalhe ? `<p class="especie">${escapar(detalhe)}</p>` : ""}
      </div>
      <div class="laudo-meta">
        ${escapar(laudo.motor)}<br>${escapar(laudo.modelo_versao)}
      </div>
    </div>
    ${figura}
    ${medidor}
    <div class="blocos">
      <div class="bloco"><h3>Sintomas observados</h3><p>${escapar(laudo.sintomas)}</p></div>
      <div class="bloco"><h3>Manejo recomendado</h3><p>${escapar(laudo.manejo)}</p></div>
      ${laudo.observacoes ? `<div class="bloco"><h3>Observações</h3><p>${escapar(laudo.observacoes)}</p></div>` : ""}
    </div>
    ${ranking}`;
}

function renderizarLaudos(laudos, nome, urlLocal = null) {
  // A miniatura do histórico é a fonte preferida: já vem com a rotação EXIF
  // corrigida e sobrevive ao descarte do objectURL local.
  const id = laudos[0]?.id_historico;
  const fonte = id != null ? `/api/historico/${id}/miniatura` : urlLocal;

  const figura = fonte
    ? `<figure class="laudo-figura">
         <div class="moldura">
           <img src="${escapar(fonte)}" alt="Foto analisada${nome ? ": " + escapar(nome) : ""}">
         </div>
         ${nome ? `<figcaption>${escapar(nome)}</figcaption>` : ""}
       </figure>`
    : "";

  // No modo comparação os dois laudos descrevem a mesma foto: ela aparece
  // só no primeiro bloco.
  painelLaudo.innerHTML =
    '<div class="laudo-acoes"><button type="button" class="botao-limpar" id="nova-analise">Analisar outra foto</button></div>' +
    laudos
      .map((laudo, i) => blocoLaudo(laudo, i === 0 ? figura : ""))
      .join('<hr style="border:0;border-top:1px solid var(--borda);margin:32px 0">');

  pintarMoldura(painelLaudo.querySelector(".laudo-figura .moldura"), fonte);

  painelLaudo.hidden = false;
  $("nova-analise").addEventListener("click", () => limparZona({ rolar: true }));
  painelLaudo.scrollIntoView({ behavior: "smooth", block: "start" });
}

/* --------------------------------------------------------------- histórico */
async function carregarHistorico() {
  let dados;
  try {
    dados = await (await fetch("/api/historico")).json();
  } catch {
    return;
  }

  $("num-total").textContent = dados.total;
  $("historico-resumo").textContent = dados.total
    ? `${dados.total} análise(s) salva(s). Clique num cartão para reabrir o laudo.`
    : "Nenhuma análise salva ainda.";

  if (!dados.itens.length) {
    grade.innerHTML = `<div class="vazio">As análises que você fizer aparecem aqui, com a foto e o laudo completo.</div>`;
    return;
  }

  grade.innerHTML = dados.itens.map((item) => {
    const cond = CONDICOES[item.condicao] || CONDICOES.indeterminado;
    return `<article class="cartao" data-id="${item.id}">
      <button class="remover" data-remover="${item.id}" title="Remover" aria-label="Remover">×</button>
      <img src="/api/historico/${item.id}/miniatura" alt="" loading="lazy">
      <div class="cartao-corpo">
        <span class="selo ${cond.selo}">${cond.texto}</span>
        <strong>${escapar(item.diagnostico)}</strong>
        <span>${escapar(item.especie || "—")} · ${escapar(item.data_legivel)}</span>
      </div>
    </article>`;
  }).join("");
}

grade.addEventListener("click", async (evento) => {
  const botaoRemover = evento.target.closest("[data-remover]");
  if (botaoRemover) {
    evento.stopPropagation();
    await fetch(`/api/historico/${botaoRemover.dataset.remover}`, { method: "DELETE" });
    await carregarHistorico();
    return;
  }

  const cartao = evento.target.closest(".cartao");
  if (!cartao) return;

  const laudo = await (await fetch(`/api/historico/${cartao.dataset.id}`)).json();
  // A zona de envio fica livre: a foto do laudo antigo aparece dentro dele.
  resetarEnvio();
  renderizarLaudos(
    [{ ...laudo, id_historico: laudo.id, ranking: [] }], laudo.arquivo);
});

$("limpar-tudo").addEventListener("click", async () => {
  if (!confirm("Apagar todo o histórico? Esta ação não pode ser desfeita.")) return;
  await fetch("/api/historico", { method: "DELETE" });
  painelLaudo.hidden = true;
  await carregarHistorico();
  avisar("Histórico apagado.", "info");
});

/* --------------------------------------------------------- arrastar/soltar */
zona.addEventListener("click", () => !analisando && entrada.click());
zona.addEventListener("keydown", (e) => {
  if (e.key === "Enter" || e.key === " ") { e.preventDefault(); entrada.click(); }
});
entrada.addEventListener("change", (e) => {
  const arquivo = e.target.files[0];
  // Zera o valor para que escolher a MESMA foto de novo volte a disparar
  // `change` — sem isso o segundo envio do mesmo arquivo é ignorado em silêncio.
  entrada.value = "";
  receber(arquivo);
});

$("trocar").addEventListener("click", (e) => { e.stopPropagation(); entrada.click(); });

// Os botões vivem dentro da zona: sem stopPropagation o clique subiria e
// reabriria o seletor de arquivos logo depois de limpar.
$("limpar-zona").addEventListener("click", (e) => { e.stopPropagation(); limparZona(); });

["dragenter", "dragover"].forEach((evt) =>
  zona.addEventListener(evt, (e) => { e.preventDefault(); zona.classList.add("arrastando"); }));

["dragleave", "drop"].forEach((evt) =>
  zona.addEventListener(evt, (e) => { e.preventDefault(); zona.classList.remove("arrastando"); }));

zona.addEventListener("drop", (e) => receber(e.dataTransfer.files[0]));

// Impede que soltar a imagem fora da zona faça o navegador abrir o arquivo.
["dragover", "drop"].forEach((evt) =>
  document.addEventListener(evt, (e) => { if (!zona.contains(e.target)) e.preventDefault(); }));

// Colar direto da área de transferência (Ctrl+V) — útil com print de celular.
document.addEventListener("paste", (e) => {
  const item = [...(e.clipboardData?.items || [])].find((i) => i.type.startsWith("image/"));
  if (item) receber(item.getAsFile());
});

/* ------------------------------------------------------------------ início */
carregarEstado().then(carregarHistorico);
