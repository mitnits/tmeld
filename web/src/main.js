// bmeld client: Meld in the browser (design: BMELD.md).
//
// Thick client, thin truth: CodeMirror owns the buffers; the server
// runs the vendored Meld engine on debounced snapshots and sends back
// the chunk model, which this file renders — line fills and inline
// marks as CM decorations, connectors and the overview map as SVG,
// exactly Meld's palette (CSS variables set from the init payload).

import {
  EditorView, lineNumbers, keymap, Decoration,
} from "@codemirror/view";
import { EditorState, StateField, StateEffect, Prec } from "@codemirror/state";
import { history, historyKeymap, defaultKeymap } from "@codemirror/commands";
import {
  calcSyncpoint, interpolateLine, scrollOffsetForLine, SCROLL_INFLUENCE,
} from "./scrollsync.js";

const REDIFF_DEBOUNCE = 250;
const CHUNK_TAGS = ["insert", "delete", "replace", "conflict", "error"];

// --- session state -----------------------------------------------------------

const token = location.pathname.split("/").pop();
const ws = new WebSocket(`ws://${location.host}/ws/${token}`);

const S = {
  numPanes: 0,
  panes: [],        // {view, titleEl, saveBtn, dirty, chunks, inline}
  pairs: [],        // pairs payload per adjacent pane pair
  emph: [],         // per pair: emphasized chunk index or null
  gutters: [],      // {el, svg}
  chunkmap: null,   // {el, svg}
  focused: 0,
  version: 0,
  awaiting: 0,      // version of the buffers message in flight
  rediffTimer: null,
  suppressScroll: new Map(), // view -> expected scrollTop
  done: false,
};

// --- decorations ---------------------------------------------------------------

const setRender = StateEffect.define();

const renderField = StateField.define({
  create: () => Decoration.none,
  update(deco, tr) {
    for (const e of tr.effects) {
      if (e.is(setRender)) return e.value;
    }
    return deco.map(tr.changes);
  },
  provide: (f) => EditorView.decorations.from(f),
});

function buildDecorations(state, chunks, inline, emphRanges) {
  const doc = state.doc;
  const ranges = [];
  const emphasized = (line) =>
    emphRanges.some(([s, e]) => line >= s && line < e);
  for (const [tag, s, e] of chunks) {
    for (let ln = s; ln < e && ln < doc.lines; ln++) {
      let cls = `bm-${tag}`;
      if (ln === s) cls += " bm-first";
      if (ln === e - 1) cls += " bm-last";
      if (emphasized(ln)) cls += " bm-emph";
      ranges.push(Decoration.line({ class: cls }).range(doc.line(ln + 1).from));
    }
  }
  for (const [ln, s, e] of inline) {
    if (ln >= doc.lines) continue;
    const line = doc.line(ln + 1);
    const from = Math.min(line.from + s, line.to);
    const to = Math.min(line.from + e, line.to);
    if (to > from) {
      ranges.push(Decoration.mark({ class: "bm-inline" }).range(from, to));
    }
  }
  return Decoration.set(ranges, true);
}

function redecorate() {
  S.panes.forEach((pane, i) => {
    const emphRanges = [];
    if (i > 0 && S.emph[i - 1] != null) {
      const c = S.pairs[i - 1][S.emph[i - 1]];
      if (c) emphRanges.push([c[3], c[4]]); // b side of pair i-1
    }
    if (i < S.numPanes - 1 && S.emph[i] != null) {
      const c = S.pairs[i][S.emph[i]];
      if (c) emphRanges.push([c[1], c[2]]); // a side of pair i
    }
    pane.view.dispatch({
      effects: setRender.of(
        buildDecorations(pane.view.state, pane.chunks, pane.inline, emphRanges)
      ),
    });
  });
}

// --- geometry helpers ------------------------------------------------------------

const lineHeight = (view) => view.defaultLineHeight;
const totalLines = (view) => view.state.doc.lines;
const pageLines = (view) => view.scrollDOM.clientHeight / lineHeight(view);
const scrollLines = (view) => view.scrollDOM.scrollTop / lineHeight(view);

// Screen y of the top of a document line (line may equal doc.lines = EOF)
function lineScreenY(view, line) {
  const doc = view.state.doc;
  if (line >= doc.lines) {
    return view.lineBlockAt(doc.length).bottom + view.documentTop;
  }
  return view.lineBlockAt(doc.line(line + 1).from).top + view.documentTop;
}

// --- connectors + gutters (SVG; geometry = upstream linkmap.py) -----------------

const svgNS = "http://www.w3.org/2000/svg";

function renderConnectors(k) {
  const { svg, el } = S.gutters[k];
  if (!S.pairs[k]) return; // first paint can precede the chunks payload
  const va = S.panes[k].view;
  const vb = S.panes[k + 1].view;
  const rect = el.getBoundingClientRect();
  const W = rect.width;
  const H = rect.height;
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  while (svg.firstChild) svg.removeChild(svg.firstChild);
  el.querySelectorAll(".bm-arrow").forEach((a) => a.remove());

  S.pairs[k].forEach((chunk, index) => {
    const [tag, sa, ea, sb, eb] = chunk;
    let f0 = lineScreenY(va, sa) - rect.top;
    let f1 = ea > sa ? lineScreenY(va, ea) - rect.top - 1 : f0;
    let t0 = lineScreenY(vb, sb) - rect.top;
    let t1 = eb > sb ? lineScreenY(vb, eb) - rect.top - 1 : t0;
    if (Math.max(f1, t1) < 0 || Math.min(f0, t0) > H) return;

    const emphasis = S.emph[k] === index;
    const path = document.createElementNS(svgNS, "path");
    const m = W / 2;
    path.setAttribute("d",
      `M -0.5 ${f0 - 0.5}` +
      ` C ${m} ${f0 - 0.5} ${m} ${t0 - 0.5} ${W + 0.5} ${t0 - 0.5}` +
      ` L ${W + 0.5} ${t1 + 0.5}` +
      ` C ${m} ${t1 + 0.5} ${m} ${f1 + 0.5} -0.5 ${f1 + 0.5} Z`);
    path.setAttribute("fill",
      `var(--bm-${tag}-${emphasis ? "emph" : "fill"})`);
    path.setAttribute("stroke", `var(--bm-${tag}-line)`);
    path.setAttribute("stroke-width", "1");
    svg.appendChild(path);

    // Push arrows on chunk-start rows (Meld's gutter buttons)
    if (sa !== ea) {
      el.appendChild(makeArrow("▶", tag, 1, f0, () =>
        pushPair(k, true, index)));
    }
    if (sb !== eb) {
      el.appendChild(makeArrow("◀", tag, W - 13, t0, () =>
        pushPair(k, false, index)));
    }
  });
}

function makeArrow(glyph, tag, x, y, onclick) {
  const btn = document.createElement("button");
  btn.className = "bm-arrow";
  btn.textContent = glyph;
  btn.style.left = `${x}px`;
  btn.style.top = `${Math.max(0, y)}px`;
  btn.style.color = `var(--bm-${tag}-fg)`;
  btn.addEventListener("mousedown", (e) => e.preventDefault());
  btn.addEventListener("click", onclick);
  return btn;
}

function renderChunkmap() {
  const { el, svg } = S.chunkmap;
  const mid = Math.min(1, S.numPanes - 1);
  const view = S.panes[mid].view;
  const H = el.clientHeight;
  const W = el.clientWidth;
  const total = Math.max(totalLines(view), 1);
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  while (svg.firstChild) svg.removeChild(svg.firstChild);
  for (const [tag, s, e] of S.panes[mid].chunks) {
    const r = document.createElementNS(svgNS, "rect");
    const y = (s / total) * H;
    r.setAttribute("x", 2);
    r.setAttribute("width", W - 4);
    r.setAttribute("y", y);
    r.setAttribute("height", Math.max(((Math.max(e, s + 1) - s) / total) * H, 2));
    r.setAttribute("fill", `var(--bm-${tag}-line)`);
    svg.appendChild(r);
  }
  const lens = document.createElementNS(svgNS, "rect");
  lens.setAttribute("x", 0);
  lens.setAttribute("width", W);
  lens.setAttribute("y", (scrollLines(view) / total) * H);
  lens.setAttribute("height", Math.max((pageLines(view) / total) * H, 4));
  lens.setAttribute("class", "bm-lens");
  svg.appendChild(lens);
}

let overlayScheduled = false;
function renderOverlays() {
  if (overlayScheduled) return;
  overlayScheduled = true;
  requestAnimationFrame(() => {
    overlayScheduled = false;
    for (let k = 0; k < S.numPanes - 1; k++) renderConnectors(k);
    renderChunkmap();
  });
}

// --- synchronized scrolling -------------------------------------------------------

function setScrollTop(view, px) {
  S.suppressScroll.set(view, px);
  view.scrollDOM.scrollTop = px;
}

function onPaneScroll(master) {
  const masterView = S.panes[master].view;
  const suppressed = S.suppressScroll.get(masterView);
  if (suppressed !== undefined &&
      Math.abs(masterView.scrollDOM.scrollTop - suppressed) < 1) {
    S.suppressScroll.delete(masterView);
    renderOverlays();
    return;
  }
  S.suppressScroll.delete(masterView);

  const totals = S.panes.map((p) => totalLines(p.view));
  let m = master;
  let value = scrollLines(masterView);
  const page = pageLines(masterView);
  const syncpoint = calcSyncpoint(value, page, totals[m]);
  let targetLine = value + page * syncpoint;

  for (const i of SCROLL_INFLUENCE[S.numPanes][master]) {
    const other = S.panes[i].view;
    const chunks = pairChunksOriented(m, i);
    const otherLine = interpolateLine(targetLine, totals[m], totals[i], chunks);
    const offset = scrollOffsetForLine(
      otherLine, pageLines(other), totals[i], syncpoint);
    setScrollTop(other, offset * lineHeight(other));
    if (i === 1) {
      m = 1;
      targetLine = otherLine;
    }
  }
  renderOverlays();
}

// pairs[k] is oriented k -> k+1; reverse when master is on the right
function pairChunksOriented(from, to) {
  const k = Math.min(from, to);
  const chunks = S.pairs[k] || [];
  if (from < to) return chunks;
  return chunks.map(([tag, sa, ea, sb, eb]) => [tag, sb, eb, sa, ea]);
}

// --- chunk actions (port of filediff._replace_lines & friends) --------------------

function getLines(pane, start, end) {
  const doc = S.panes[pane].view.state.doc;
  const out = [];
  for (let ln = start; ln < end && ln < doc.lines; ln++) {
    out.push(doc.line(ln + 1).text);
  }
  return out;
}

function replaceLines(view, start, end, newLines) {
  const doc = view.state.doc;
  let from, to, insert;
  if (end < doc.lines) {
    from = doc.line(start + 1).from;
    to = doc.line(end + 1).from;
    insert = newLines.map((l) => l + "\n").join("");
  } else {
    // chunk reaches EOF: splice without a trailing newline
    insert = newLines.join("\n");
    if (start > 0) {
      from = doc.line(start).to;
      insert = newLines.length ? "\n" + insert : "";
    } else {
      from = 0;
    }
    to = doc.length;
  }
  view.dispatch({ changes: { from, to, insert } });
}

function pushPair(k, srcIsA, index) {
  const chunk = S.pairs[k] && S.pairs[k][index];
  if (!chunk) return bell();
  const [, sa, ea, sb, eb] = chunk;
  if (srcIsA) {
    replaceLines(S.panes[k + 1].view, sb, eb, getLines(k, sa, ea));
  } else {
    replaceLines(S.panes[k].view, sa, ea, getLines(k + 1, sb, eb));
  }
}

function cursorRow(pane) {
  const view = S.panes[pane].view;
  return view.state.doc.lineAt(view.state.selection.main.head).number - 1;
}

function pairChunkAtCursor(k, side /* 'a'|'b' */, pane) {
  const row = cursorRow(pane);
  const chunks = S.pairs[k] || [];
  for (let i = 0; i < chunks.length; i++) {
    const [, sa, ea, sb, eb] = chunks[i];
    const [s, e] = side === "a" ? [sa, ea] : [sb, eb];
    if (row >= s && (row < e || (s === e && row === s))) return i;
  }
  return null;
}

function pushAction(direction) {
  let src, dst;
  if (S.numPanes === 2) {
    [src, dst] = direction > 0 ? [0, 1] : [1, 0]; // Meld: ignores focus
  } else {
    src = S.focused;
    dst = src + direction;
    if (dst < 0 || dst >= S.numPanes) return bell();
  }
  const k = Math.min(src, dst);
  const srcIsA = src === k;
  const cursorPane = S.numPanes === 2 ? S.focused : src;
  const side = cursorPane === k ? "a" : "b";
  const index = pairChunkAtCursor(k, side, cursorPane);
  if (index == null) return bell();
  pushPair(k, srcIsA, index);
}

function pullAction(direction) {
  // pull FROM the neighbor in `direction` INTO the focused pane
  const src = S.focused + direction;
  const dst = S.focused;
  if (src < 0 || src >= S.numPanes) return bell();
  const k = Math.min(src, dst);
  const side = dst === k ? "a" : "b";
  const index = pairChunkAtCursor(k, side, dst);
  if (index == null) return bell();
  pushPair(k, src === k, index);
}

function deleteChunkAction() {
  const pane = S.focused;
  const row = cursorRow(pane);
  const chunk = S.panes[pane].chunks.find(
    ([, s, e]) => row >= s && row < e);
  if (!chunk || chunk[1] === chunk[2]) return bell();
  replaceLines(S.panes[pane].view, chunk[1], chunk[2], []);
}

function navChunk(delta) {
  const pane = S.focused;
  const row = cursorRow(pane);
  const chunks = S.panes[pane].chunks;
  let target = null;
  if (delta > 0) {
    target = chunks.find(([, s]) => s > row);
  } else {
    for (const c of chunks) if (c[2] <= row) target = c;
  }
  if (!target) return bell();
  goToLine(pane, target[1]);
}

function navConflict(delta) {
  const pane = S.focused;
  const row = cursorRow(pane);
  const conflicts = S.panes[pane].chunks.filter(([t]) => t === "conflict");
  let target = null;
  if (delta > 0) target = conflicts.find(([, s]) => s > row);
  else for (const c of conflicts) if (c[2] <= row) target = c;
  if (!target) return bell();
  goToLine(pane, target[1]);
}

function goToLine(pane, line) {
  const view = S.panes[pane].view;
  const pos = view.state.doc.line(Math.min(line + 1, view.state.doc.lines)).from;
  view.dispatch({
    selection: { anchor: pos },
    effects: EditorView.scrollIntoView(pos, { y: "center" }),
  });
  view.focus();
}

function mergeAllAction() {
  if (S.numPanes !== 3) return bell();
  send({
    type: "merge_all",
    texts: S.panes.map((p) => p.view.state.doc.toString()),
  });
}

function bell() {
  const el = document.getElementById("stats");
  el.classList.remove("bm-bell");
  void el.offsetWidth; // restart animation
  el.classList.add("bm-bell");
}

// --- save / status ------------------------------------------------------------------

function savePane(pane) {
  send({
    type: "save",
    pane,
    text: S.panes[pane].view.state.doc.toString(),
  });
}

function markDirty(i, dirty) {
  S.panes[i].dirty = dirty;
  S.panes[i].titleEl.classList.toggle("bm-dirty", dirty);
  S.panes[i].saveBtn.style.visibility = dirty ? "visible" : "hidden";
}

function setStatus(text) {
  document.getElementById("stats").textContent = text;
}

// --- websocket ---------------------------------------------------------------------

function send(obj) {
  if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(obj));
}

function scheduleRediff() {
  clearTimeout(S.rediffTimer);
  S.rediffTimer = setTimeout(() => {
    S.awaiting = ++S.version;
    send({
      type: "buffers",
      version: S.version,
      texts: S.panes.map((p) => p.view.state.doc.toString()),
    });
  }, REDIFF_DEBOUNCE);
}

ws.onmessage = (event) => {
  const msg = JSON.parse(event.data);
  if (msg.type === "init") return onInit(msg);
  if (msg.type === "chunks") return onChunks(msg);
  if (msg.type === "saved") {
    const i = msg.pane;
    markDirty(i, false);
    setStatus(`Saved ${msg.path}`);
    return;
  }
  if (msg.type === "merged") {
    const view = S.panes[1].view;
    view.dispatch({
      changes: { from: 0, to: view.state.doc.length, insert: msg.text },
    });
    return;
  }
  if (msg.type === "error") setStatus(`error: ${msg.message}`);
};

ws.onclose = () => {
  if (!S.done) {
    document.getElementById("banner").textContent =
      "connection lost — reload to reconnect";
    document.getElementById("banner").style.display = "block";
  }
};

function onChunks(msg) {
  if (msg.version !== undefined && msg.version !== S.awaiting) return;
  msg.panes.forEach((chunks, i) => {
    S.panes[i].chunks = chunks;
    S.panes[i].inline = msg.inline[i];
  });
  S.pairs = msg.pairs;
  S.emph = S.emph.length ? S.emph : new Array(msg.pairs.length).fill(null);
  updateEmphasisFromCursor(S.focused, false);
  redecorate();
  renderOverlays();
  let status = msg.diff_count === 0 ? "identical" : `${msg.diff_count} changes`;
  if (msg.conflict_count) status += `, ${msg.conflict_count} conflicts`;
  setStatus(status);
}

// --- emphasis (cursor chunk) ----------------------------------------------------------

function updateEmphasisFromCursor(pane, repaint = true) {
  const row = S.panes[pane] ? cursorRow(pane) : 0;
  let changed = false;
  for (let k = 0; k < S.numPanes - 1; k++) {
    let idx = null;
    if (pane === k || pane === k + 1) {
      idx = pairChunkAtCursor(k, pane === k ? "a" : "b", pane);
    } else {
      idx = S.emph[k];
    }
    if (idx !== S.emph[k]) {
      S.emph[k] = idx;
      changed = true;
    }
  }
  if (changed && repaint) {
    redecorate();
    renderOverlays();
  }
}

// --- UI construction -------------------------------------------------------------------

function onInit(msg) {
  S.numPanes = msg.num_panes;
  const pal = msg.palette;
  const root = document.documentElement.style;
  root.setProperty("--bm-page-bg", pal.page_bg || "#ffffff");
  root.setProperty("--bm-text-fg", pal.text_fg);
  root.setProperty("--bm-unknown-fg", pal.unknown_fg);
  root.setProperty("--bm-inline-bg", pal.inline_bg);
  root.setProperty("--bm-overlay", pal.overlay_color);
  root.setProperty("--bm-overlay-alpha", pal.overlay_alpha);
  for (const tag of CHUNK_TAGS) {
    const c = pal.chunk[tag];
    if (!c) continue;
    root.setProperty(`--bm-${tag}-fill`, c.fill);
    root.setProperty(`--bm-${tag}-line`, c.line);
    root.setProperty(`--bm-${tag}-fg`, c.fg);
    root.setProperty(`--bm-${tag}-emph`, c.emphasis);
  }

  const main = document.getElementById("main");
  const cols = [];
  for (let i = 0; i < S.numPanes; i++) {
    if (i) cols.push("38px");
    cols.push("1fr");
  }
  cols.push("22px");
  main.style.gridTemplateColumns = cols.join(" ");

  for (let i = 0; i < S.numPanes; i++) {
    if (i) {
      const g = document.createElement("div");
      g.className = "bm-gutter";
      const svg = document.createElementNS(svgNS, "svg");
      g.appendChild(svg);
      main.appendChild(g);
      S.gutters.push({ el: g, svg });
    }
    main.appendChild(makePane(i, msg));
  }
  const cm = document.createElement("div");
  cm.id = "chunkmap";
  const svg = document.createElementNS(svgNS, "svg");
  cm.appendChild(svg);
  cm.addEventListener("click", (e) => {
    const mid = Math.min(1, S.numPanes - 1);
    const total = totalLines(S.panes[mid].view);
    goToLine(mid, Math.floor((e.offsetY / cm.clientHeight) * total));
  });
  main.appendChild(cm);
  S.chunkmap = { el: cm, svg };

  document.getElementById("done").addEventListener("click", () => {
    S.done = true;
    send({ type: "close" });
    document.getElementById("banner").textContent =
      "session ended — you can close this tab";
    document.getElementById("banner").style.display = "block";
  });

  window.addEventListener("resize", renderOverlays);
  S.panes[0].view.focus();
  renderOverlays();
}

function makePane(i, msg) {
  const cell = document.createElement("div");
  cell.className = "bm-pane";
  const title = document.createElement("div");
  title.className = "bm-title";
  const label = document.createElement("span");
  label.textContent = msg.labels[i];
  label.title = msg.paths[i];
  const save = document.createElement("button");
  save.textContent = "Save";
  save.className = "bm-save";
  save.style.visibility = "hidden";
  save.addEventListener("click", () => savePane(i));
  title.appendChild(label);
  title.appendChild(save);

  const editorHost = document.createElement("div");
  editorHost.className = "bm-editor";

  const view = new EditorView({
    state: EditorState.create({
      doc: msg.texts[i],
      extensions: [
        lineNumbers(),
        history(),
        renderField,
        Prec.high(keymap.of(meldKeymap(i))),
        keymap.of([...defaultKeymap, ...historyKeymap]),
        EditorView.updateListener.of((update) => {
          if (update.docChanged) {
            markDirty(i, true);
            scheduleRediff();
          }
          if (update.selectionSet || update.focusChanged) {
            if (update.view.hasFocus) S.focused = i;
            updateEmphasisFromCursor(i);
          }
        }),
        EditorView.theme({
          "&": { height: "100%" },
        }),
      ],
    }),
    parent: editorHost,
  });
  view.scrollDOM.addEventListener("scroll", () => onPaneScroll(i));

  cell.appendChild(title);
  cell.appendChild(editorHost);
  S.panes[i] = {
    view, titleEl: title, saveBtn: save,
    dirty: false, chunks: [], inline: [],
  };
  return cell;
}

function meldKeymap(pane) {
  const bind = (run) => () => { run(); return true; };
  return [
    { key: "Alt-ArrowDown", run: bind(() => navChunk(1)) },
    { key: "Ctrl-d", run: bind(() => navChunk(1)) },
    { key: "Alt-ArrowUp", run: bind(() => navChunk(-1)) },
    { key: "Ctrl-e", run: bind(() => navChunk(-1)) },
    { key: "Alt-ArrowRight", run: bind(() => pushAction(1)) },
    { key: "Alt-ArrowLeft", run: bind(() => pushAction(-1)) },
    { key: "Shift-Alt-ArrowRight", run: bind(() => pullAction(-1)) },
    { key: "Shift-Alt-ArrowLeft", run: bind(() => pullAction(1)) },
    { key: "Alt-Delete", run: bind(deleteChunkAction) },
    { key: "Ctrl-k", run: bind(() => navConflict(1)) },
    { key: "Ctrl-j", run: bind(() => navConflict(-1)) },
    { key: "Alt-m", run: bind(mergeAllAction) },
    { key: "Mod-s", run: bind(() => savePane(S.focused)) },
  ];
}
