// bmeld client: Meld in the browser (design: BMELD.md).
//
// Thick client, thin truth: CodeMirror owns file buffers; the server
// runs the vendored Meld engine (rediff, merge-all, dir scans) and is
// the only thing that touches disk. This file renders the chunk model
// — line fills and inline marks as CM decorations, connectors and the
// overview map as SVG, dir comparisons as a state-styled tree — in
// exactly Meld's palette (CSS variables set from the init payload).
//
// Tabs mirror the TUI shell: FileTab (2/3-way editors) and DirTab
// (tree; Enter/double-click opens a FileTab via the server).

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

// meld/vc/_vc.py state constants -> CSS class suffixes (PARITY.md §2)
const STATE_CLASSES = {
  0: "ignored", 1: "none", 2: "normal", 3: "nochange", 4: "error",
  5: "empty", 6: "new", 7: "modified", 8: "renamed", 9: "conflict",
  10: "removed", 11: "missing", 12: "nonexist", 13: "spinner",
};

const token = location.pathname.split("/").pop();
const ws = new WebSocket(`ws://${location.host}/ws/${token}`);

const session = {
  tabs: new Map(),      // id -> FileTab | DirTab
  order: [],
  active: null,         // tab id
  done: false,
};

function send(obj) {
  if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(obj));
}

function bell() {
  const el = document.getElementById("stats");
  el.classList.remove("bm-bell");
  void el.offsetWidth;
  el.classList.add("bm-bell");
}

function setStatus(text) {
  document.getElementById("stats").textContent = text;
}

// --- CM decorations -----------------------------------------------------------

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

// --- FileTab --------------------------------------------------------------------

const svgNS = "http://www.w3.org/2000/svg";

class FileTab {
  constructor(container, data) {
    this.id = data.id;
    this.kind = "file";
    this.label = data.label;
    this.numPanes = data.num_panes;
    this.paths = data.paths;
    this.container = container;
    this.panes = [];
    this.gutters = [];
    this.pairs = [];
    this.emph = [];
    this.focused = 0;
    this.version = 0;
    this.awaiting = 0;
    this.rediffTimer = null;
    this.suppressScroll = new Map();
    this.status = "…";
    this.build(data);
  }

  build(data) {
    const grid = document.createElement("div");
    grid.className = "bm-filegrid";
    const cols = [];
    for (let i = 0; i < this.numPanes; i++) {
      if (i) cols.push("38px");
      cols.push("1fr");
    }
    cols.push("22px");
    grid.style.gridTemplateColumns = cols.join(" ");

    for (let i = 0; i < this.numPanes; i++) {
      if (i) {
        const g = document.createElement("div");
        g.className = "bm-gutter";
        const svg = document.createElementNS(svgNS, "svg");
        g.appendChild(svg);
        grid.appendChild(g);
        this.gutters.push({ el: g, svg });
      }
      grid.appendChild(this.makePane(i, data));
    }
    const cm = document.createElement("div");
    cm.className = "bm-chunkmap";
    const svg = document.createElementNS(svgNS, "svg");
    cm.appendChild(svg);
    cm.addEventListener("click", (e) => {
      const mid = Math.min(1, this.numPanes - 1);
      const total = this.totalLines(mid);
      this.goToLine(mid, Math.floor((e.offsetY / cm.clientHeight) * total));
    });
    grid.appendChild(cm);
    this.chunkmap = { el: cm, svg };
    this.container.appendChild(grid);
  }

  makePane(i, data) {
    const cell = document.createElement("div");
    // Position class drives which side this pane's scrollbar sits on, so that
    // no scrollbar ever lands against a linkmap gutter. See bmeld.css.
    const where = i === 0 ? "first"
      : i === this.numPanes - 1 ? "last" : "mid";
    cell.className = `bm-pane bm-pane-${where}`;
    const title = document.createElement("div");
    title.className = "bm-title";
    const label = document.createElement("span");
    label.textContent = data.labels[i];
    label.title = data.paths[i];
    const save = document.createElement("button");
    save.textContent = "Save";
    save.className = "bm-save";
    save.style.visibility = "hidden";
    save.addEventListener("click", () => this.savePane(i));
    title.appendChild(label);
    title.appendChild(save);

    const host = document.createElement("div");
    host.className = "bm-editor";
    const tab = this;
    const readonly = (data.readonly || []).includes(i);
    const extensions = [
      lineNumbers(),
      history(),
      renderField,
      Prec.high(keymap.of(meldKeymap())),
      keymap.of([...defaultKeymap, ...historyKeymap]),
      EditorView.updateListener.of((update) => {
        if (update.docChanged) {
          tab.markDirty(i, true);
          tab.scheduleRediff();
        }
        if (update.selectionSet || update.focusChanged) {
          if (update.view.hasFocus) tab.focused = i;
          tab.updateEmphasisFromCursor(i);
        }
      }),
      EditorView.theme({ "&": { height: "100%" } }),
    ];
    if (readonly) {
      extensions.push(EditorState.readOnly.of(true));
      extensions.push(EditorView.editable.of(false));
      title.classList.add("bm-readonly");
    }
    const view = new EditorView({
      state: EditorState.create({ doc: data.texts[i], extensions }),
      parent: host,
    });
    view.scrollDOM.addEventListener("scroll", () => this.onPaneScroll(i));

    cell.appendChild(title);
    cell.appendChild(host);
    this.panes[i] = {
      view, titleEl: title, saveBtn: save,
      dirty: false, chunks: [], inline: [],
    };
    return cell;
  }

  activate() {
    for (const pane of this.panes) pane.view.requestMeasure();
    this.renderOverlays();
    this.panes[0].view.focus();
  }

  // --- geometry ---------------------------------------------------------------

  totalLines(i) { return this.panes[i].view.state.doc.lines; }
  lineHeight(i) { return this.panes[i].view.defaultLineHeight; }
  pageLines(i) {
    return this.panes[i].view.scrollDOM.clientHeight / this.lineHeight(i);
  }
  scrollLinesOf(i) {
    return this.panes[i].view.scrollDOM.scrollTop / this.lineHeight(i);
  }

  lineScreenY(view, line) {
    const doc = view.state.doc;
    if (line >= doc.lines) {
      return view.lineBlockAt(doc.length).bottom + view.documentTop;
    }
    return view.lineBlockAt(doc.line(line + 1).from).top + view.documentTop;
  }

  // --- chunk payload ------------------------------------------------------------

  onChunks(msg) {
    if (msg.version !== undefined && msg.version !== this.awaiting) return;
    msg.panes.forEach((chunks, i) => {
      this.panes[i].chunks = chunks;
      this.panes[i].inline = msg.inline[i];
    });
    this.pairs = msg.pairs;
    if (!this.emph.length) this.emph = new Array(msg.pairs.length).fill(null);
    this.updateEmphasisFromCursor(this.focused, false);
    this.redecorate();
    this.renderOverlays();
    let status = msg.diff_count === 0
      ? "identical" : `${msg.diff_count} changes`;
    if (msg.conflict_count) status += `, ${msg.conflict_count} conflicts`;
    this.status = status;
    if (session.active === this.id) setStatus(status);
  }

  redecorate() {
    this.panes.forEach((pane, i) => {
      const emphRanges = [];
      if (i > 0 && this.emph[i - 1] != null) {
        const c = this.pairs[i - 1][this.emph[i - 1]];
        if (c) emphRanges.push([c[3], c[4]]);
      }
      if (i < this.numPanes - 1 && this.emph[i] != null) {
        const c = this.pairs[i][this.emph[i]];
        if (c) emphRanges.push([c[1], c[2]]);
      }
      pane.view.dispatch({
        effects: setRender.of(buildDecorations(
          pane.view.state, pane.chunks, pane.inline, emphRanges)),
      });
    });
  }

  // --- overlays -------------------------------------------------------------------

  renderOverlays() {
    if (this._overlayScheduled) return;
    this._overlayScheduled = true;
    requestAnimationFrame(() => {
      this._overlayScheduled = false;
      if (session.active !== this.id) return;
      for (let k = 0; k < this.numPanes - 1; k++) this.renderConnectors(k);
      this.renderChunkmap();
    });
  }

  renderConnectors(k) {
    const { svg, el } = this.gutters[k];
    if (!this.pairs[k]) return;
    const va = this.panes[k].view;
    const vb = this.panes[k + 1].view;
    const rect = el.getBoundingClientRect();
    const W = rect.width;
    const H = rect.height;
    if (!W || !H) return;
    // No viewBox: user units are CSS pixels of the gutter box, 1:1, with no
    // scale and no translate. A viewBox whose height lags the element's (the
    // gutter resizes without a re-render) would otherwise be centered by the
    // default preserveAspectRatio and silently shift every connector by half
    // the difference.
    while (svg.firstChild) svg.removeChild(svg.firstChild);
    el.querySelectorAll(".bm-arrow").forEach((a) => a.remove());

    this.pairs[k].forEach((chunk, index) => {
      const [tag, sa, ea, sb, eb] = chunk;
      const [f0, f1] = this.chunkEdges(va, sa, ea, rect.top);
      const [t0, t1] = this.chunkEdges(vb, sb, eb, rect.top);
      if (Math.max(f1, t1) < 0 || Math.min(f0, t0) > H) return;

      const emphasis = this.emph[k] === index;
      const path = document.createElementNS(svgNS, "path");
      const m = W / 2;
      path.setAttribute("d",
        `M -0.5 ${f0}` +
        ` C ${m} ${f0} ${m} ${t0} ${W + 0.5} ${t0}` +
        ` L ${W + 0.5} ${t1}` +
        ` C ${m} ${t1} ${m} ${f1} -0.5 ${f1} Z`);
      path.setAttribute("fill",
        `var(--bm-${tag}-${emphasis ? "emph" : "fill"})`);
      path.setAttribute("stroke", `var(--bm-${tag}-line)`);
      path.setAttribute("stroke-width", "1");
      svg.appendChild(path);

      if (sa !== ea) {
        el.appendChild(this.makeArrow("▶", tag, 1, f0, () =>
          this.pushPair(k, true, index)));
      }
      if (sb !== eb) {
        el.appendChild(this.makeArrow("◀", tag, W - 15, t0, () =>
          this.pushPair(k, false, index)));
      }
    });
  }

  // Line tops land on fractional pixels (line-height 1.45 of 13px). The
  // browser snaps CSS backgrounds and box-shadows to the device-pixel grid
  // but antialiases SVG, so a connector drawn at the raw coordinate is a
  // fraction of a pixel off the fill it attaches to. Snap to the same grid.
  snapY(pageY) {
    const dpr = window.devicePixelRatio || 1;
    return Math.round(pageY * dpr) / dpr;
  }

  // Stroke centerlines for a chunk's top and bottom edges, in gutter
  // coordinates. Upstream's cairo nudges (f0 - 0.5, f1 - 1 + 0.5) center a
  // 1px line *straddling* the line boundary; our pane boundaries are CSS
  // `box-shadow: inset`, painted just *inside* the fill. So inset the
  // centerlines by half a pixel instead, and the connector's stroke lands on
  // exactly the rows the box-shadow does. A zero-height chunk (a pure
  // insertion on the other side) has no fill to align with: keep it a 1px
  // band straddling the boundary it points at.
  chunkEdges(view, start, end, offset) {
    const top = this.snapY(this.lineScreenY(view, start)) - offset;
    if (end <= start) return [top - 0.5, top + 0.5];
    const bottom = this.snapY(this.lineScreenY(view, end)) - offset;
    if (bottom - top < 1) {
      const mid = (top + bottom) / 2;
      return [mid - 0.5, mid + 0.5];
    }
    return [top + 0.5, bottom - 0.5];
  }

  makeArrow(glyph, tag, x, y, onclick) {
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

  renderChunkmap() {
    const { el, svg } = this.chunkmap;
    const mid = Math.min(1, this.numPanes - 1);
    const H = el.clientHeight;
    const W = el.clientWidth;
    if (!W || !H) return;
    const total = Math.max(this.totalLines(mid), 1);
    while (svg.firstChild) svg.removeChild(svg.firstChild);
    for (const [tag, s, e] of this.panes[mid].chunks) {
      const r = document.createElementNS(svgNS, "rect");
      r.setAttribute("x", 2);
      r.setAttribute("width", W - 4);
      r.setAttribute("y", (s / total) * H);
      r.setAttribute("height",
        Math.max(((Math.max(e, s + 1) - s) / total) * H, 2));
      r.setAttribute("fill", `var(--bm-${tag}-line)`);
      svg.appendChild(r);
    }
    const lens = document.createElementNS(svgNS, "rect");
    lens.setAttribute("x", 0);
    lens.setAttribute("width", W);
    lens.setAttribute("y", (this.scrollLinesOf(mid) / total) * H);
    lens.setAttribute("height",
      Math.max((this.pageLines(mid) / total) * H, 4));
    lens.setAttribute("class", "bm-lens");
    svg.appendChild(lens);
  }

  // --- scrolling ---------------------------------------------------------------------

  onPaneScroll(master) {
    const masterView = this.panes[master].view;
    const suppressed = this.suppressScroll.get(masterView);
    if (suppressed !== undefined &&
        Math.abs(masterView.scrollDOM.scrollTop - suppressed) < 1) {
      this.suppressScroll.delete(masterView);
      this.renderOverlays();
      return;
    }
    this.suppressScroll.delete(masterView);

    const totals = this.panes.map((_, i) => this.totalLines(i));
    let m = master;
    const value = this.scrollLinesOf(master);
    const page = this.pageLines(master);
    const syncpoint = calcSyncpoint(value, page, totals[m]);
    let targetLine = value + page * syncpoint;

    for (const i of SCROLL_INFLUENCE[this.numPanes][master]) {
      const other = this.panes[i].view;
      const chunks = this.pairChunksOriented(m, i);
      const otherLine = interpolateLine(
        targetLine, totals[m], totals[i], chunks);
      const offset = scrollOffsetForLine(
        otherLine, this.pageLines(i), totals[i], syncpoint);
      this.suppressScroll.set(other, offset * this.lineHeight(i));
      other.scrollDOM.scrollTop = offset * this.lineHeight(i);
      if (i === 1) {
        m = 1;
        targetLine = otherLine;
      }
    }
    this.renderOverlays();
  }

  pairChunksOriented(from, to) {
    const k = Math.min(from, to);
    const chunks = this.pairs[k] || [];
    if (from < to) return chunks;
    return chunks.map(([tag, sa, ea, sb, eb]) => [tag, sb, eb, sa, ea]);
  }

  // --- actions -------------------------------------------------------------------------

  getLines(pane, start, end) {
    const doc = this.panes[pane].view.state.doc;
    const out = [];
    for (let ln = start; ln < end && ln < doc.lines; ln++) {
      out.push(doc.line(ln + 1).text);
    }
    return out;
  }

  replaceLines(view, start, end, newLines) {
    const doc = view.state.doc;
    let from, to, insert;
    if (end < doc.lines) {
      from = doc.line(start + 1).from;
      to = doc.line(end + 1).from;
      insert = newLines.map((l) => l + "\n").join("");
    } else {
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

  pushPair(k, srcIsA, index) {
    const chunk = this.pairs[k] && this.pairs[k][index];
    if (!chunk) return bell();
    const [, sa, ea, sb, eb] = chunk;
    if (srcIsA) {
      this.replaceLines(this.panes[k + 1].view, sb, eb,
        this.getLines(k, sa, ea));
    } else {
      this.replaceLines(this.panes[k].view, sa, ea,
        this.getLines(k + 1, sb, eb));
    }
  }

  cursorRow(pane) {
    const view = this.panes[pane].view;
    return view.state.doc.lineAt(view.state.selection.main.head).number - 1;
  }

  pairChunkAtCursor(k, side, pane) {
    const row = this.cursorRow(pane);
    const chunks = this.pairs[k] || [];
    for (let i = 0; i < chunks.length; i++) {
      const [, sa, ea, sb, eb] = chunks[i];
      const [s, e] = side === "a" ? [sa, ea] : [sb, eb];
      if (row >= s && (row < e || (s === e && row === s))) return i;
    }
    return null;
  }

  pushAction(direction) {
    let src, dst;
    if (this.numPanes === 2) {
      [src, dst] = direction > 0 ? [0, 1] : [1, 0]; // Meld: ignores focus
    } else {
      src = this.focused;
      dst = src + direction;
      if (dst < 0 || dst >= this.numPanes) return bell();
    }
    const k = Math.min(src, dst);
    const srcIsA = src === k;
    const cursorPane = this.numPanes === 2 ? this.focused : src;
    const side = cursorPane === k ? "a" : "b";
    const index = this.pairChunkAtCursor(k, side, cursorPane);
    if (index == null) return bell();
    this.pushPair(k, srcIsA, index);
  }

  pullAction(direction) {
    const src = this.focused + direction;
    const dst = this.focused;
    if (src < 0 || src >= this.numPanes) return bell();
    const k = Math.min(src, dst);
    const side = dst === k ? "a" : "b";
    const index = this.pairChunkAtCursor(k, side, dst);
    if (index == null) return bell();
    this.pushPair(k, src === k, index);
  }

  deleteChunkAction() {
    const pane = this.focused;
    const row = this.cursorRow(pane);
    const chunk = this.panes[pane].chunks.find(
      ([, s, e]) => row >= s && row < e);
    if (!chunk || chunk[1] === chunk[2]) return bell();
    this.replaceLines(this.panes[pane].view, chunk[1], chunk[2], []);
  }

  navChunk(delta) {
    const pane = this.focused;
    const row = this.cursorRow(pane);
    const chunks = this.panes[pane].chunks;
    let target = null;
    if (delta > 0) target = chunks.find(([, s]) => s > row);
    else for (const c of chunks) if (c[2] <= row) target = c;
    if (!target) return bell();
    this.goToLine(pane, target[1]);
  }

  navConflict(delta) {
    const pane = this.focused;
    const row = this.cursorRow(pane);
    const conflicts = this.panes[pane].chunks.filter(
      ([t]) => t === "conflict");
    let target = null;
    if (delta > 0) target = conflicts.find(([, s]) => s > row);
    else for (const c of conflicts) if (c[2] <= row) target = c;
    if (!target) return bell();
    this.goToLine(pane, target[1]);
  }

  goToLine(pane, line) {
    const view = this.panes[pane].view;
    const pos = view.state.doc.line(
      Math.min(line + 1, view.state.doc.lines)).from;
    view.dispatch({
      selection: { anchor: pos },
      effects: EditorView.scrollIntoView(pos, { y: "center" }),
    });
    view.focus();
  }

  mergeAllAction() {
    if (this.numPanes !== 3) return bell();
    send({
      type: "merge_all", tab: this.id,
      texts: this.panes.map((p) => p.view.state.doc.toString()),
    });
  }

  // --- save / dirty / rediff -------------------------------------------------------------

  savePane(pane) {
    send({
      type: "save", tab: this.id, pane,
      text: this.panes[pane].view.state.doc.toString(),
    });
  }

  saveFocused() { this.savePane(this.focused); }

  markDirty(i, dirty) {
    this.panes[i].dirty = dirty;
    this.panes[i].titleEl.classList.toggle("bm-dirty", dirty);
    this.panes[i].saveBtn.style.visibility = dirty ? "visible" : "hidden";
    tabBar.refreshLabel(this);
  }

  get dirty() { return this.panes.some((p) => p.dirty); }

  scheduleRediff() {
    clearTimeout(this.rediffTimer);
    this.rediffTimer = setTimeout(() => {
      this.awaiting = ++this.version;
      send({
        type: "buffers", tab: this.id, version: this.version,
        texts: this.panes.map((p) => p.view.state.doc.toString()),
      });
    }, REDIFF_DEBOUNCE);
  }

  updateEmphasisFromCursor(pane, repaint = true) {
    let changed = false;
    for (let k = 0; k < this.numPanes - 1; k++) {
      let idx = this.emph[k];
      if (pane === k || pane === k + 1) {
        idx = this.pairChunkAtCursor(k, pane === k ? "a" : "b", pane);
      }
      if (idx !== this.emph[k]) {
        this.emph[k] = idx;
        changed = true;
      }
    }
    if (changed && repaint) {
      this.redecorate();
      this.renderOverlays();
    }
  }
}

// --- DirTab / VcTab -----------------------------------------------------------------

class DirTab {
  constructor(container, data) {
    this.id = data.id;
    this.kind = data.kind;
    this.label = data.label;
    this.numPanes = data.num_panes;
    this.container = container;
    this.status = "scanning…";
    this.selected = null; // entry payload of the selected row
    this.dirty = false;

    const bar = document.createElement("div");
    bar.className = "bm-title";
    const label = document.createElement("span");
    label.textContent = data.roots.join("  —  ");
    bar.appendChild(label);
    for (const [text, handler] of this.barButtons(data)) {
      const btn = document.createElement("button");
      btn.textContent = text;
      btn.className = "bm-save";
      btn.addEventListener("click", handler);
      bar.appendChild(btn);
    }
    container.appendChild(bar);

    this.treeEl = document.createElement("div");
    this.treeEl.className = "bm-tree";
    this.treeEl.tabIndex = 0;
    this.treeEl.addEventListener("keydown", (e) => this.onKey(e));
    container.appendChild(this.treeEl);
  }

  barButtons() {
    return [["Rescan", () => send({ type: "scan", tab: this.id })]];
  }

  activate() { this.treeEl.focus(); }

  onTree(msg) {
    this.root = msg.root;
    this.status = msg.differences === 0
      ? "identical" : `${msg.differences} differences`;
    if (session.active === this.id) setStatus(this.status);
    this.render();
  }

  hasDifference(entry) {
    return entry.different ||
      entry.children.some((c) => c.different || this.hasDifference(c));
  }

  render() {
    this.treeEl.textContent = "";
    if (!this.root) return;
    const addRow = (entry, depth) => {
      const row = document.createElement("div");
      row.className = "bm-treerow";
      if (this.selected && this.selected.paths[0] === entry.paths[0] &&
          this.selected.names[0] === entry.names[0]) {
        row.classList.add("bm-selected");
      }
      entry._expanded = entry._expanded ??
        (entry.isdir && this.hasDifference(entry));
      for (let pane = 0; pane < this.numPanes; pane++) {
        const cell = document.createElement("span");
        cell.className = "bm-treecell";
        const state = entry.exists[pane] ? entry.state : 12; // NONEXIST
        cell.classList.add(`bm-st-${STATE_CLASSES[state] || "normal"}`);
        const glyph = entry.isdir ? (entry._expanded ? "▾ " : "▸ ") : "  ";
        cell.textContent =
          " ".repeat(depth * 2) + glyph + entry.names[pane];
        row.appendChild(cell);
      }
      row.addEventListener("click", () => {
        this.selected = entry;
        if (entry.isdir) entry._expanded = !entry._expanded;
        this.render();
      });
      row.addEventListener("dblclick", () => this.open(entry));
      this.treeEl.appendChild(row);
      if (entry.isdir && entry._expanded) {
        for (const child of entry.children) addRow(child, depth + 1);
      }
    };
    addRow(this.root, 0);
  }

  onKey(e) {
    if (e.key === "Enter" && this.selected) {
      e.preventDefault();
      if (this.selected.isdir) {
        this.selected._expanded = !this.selected._expanded;
        this.render();
      } else {
        this.open(this.selected);
      }
    }
  }

  open(entry) {
    if (entry.isdir) return;
    const paths = entry.paths.filter((_, i) => entry.exists[i]);
    if (paths.length < 2) return bell();
    send({ type: "open_file", paths });
  }
}

class VcTab extends DirTab {
  constructor(container, data) {
    super(container, data);
    this.commitPrefill = data.commit_prefill || "";
  }

  barButtons(data) {
    return [
      ["Rescan", () => send({ type: "scan", tab: this.id })],
      ["Commit", () => {
        const message = prompt(
          "Commit message:", (data && data.commit_prefill) || "");
        if (message && message.trim()) {
          send({ type: "vc_commit", tab: this.id, message: message.trim() });
        }
      }],
      ["Revert", () => {
        if (!this.selected || this.selected.isdir) return bell();
        const path = this.selected.paths[0];
        if (confirm(`Revert ${path}?`)) {
          send({ type: "vc_revert", tab: this.id, path });
        }
      }],
    ];
  }

  open(entry) {
    if (entry.isdir) return;
    send({ type: "vc_diff", tab: this.id, path: entry.paths[0] });
  }
}

// --- tab bar ----------------------------------------------------------------------

class TabBar {
  constructor() {
    this.el = document.getElementById("tabbar");
  }

  refresh() {
    this.el.textContent = "";
    this.el.style.display = session.order.length > 1 ? "flex" : "none";
    for (const id of session.order) {
      const tab = session.tabs.get(id);
      const btn = document.createElement("div");
      btn.className = "bm-tab" +
        (session.active === id ? " bm-tab-active" : "");
      const label = document.createElement("span");
      label.textContent = tab.label + (tab.dirty ? " •" : "");
      btn.appendChild(label);
      const close = document.createElement("span");
      close.className = "bm-tabclose";
      close.textContent = "✕";
      close.addEventListener("click", (e) => {
        e.stopPropagation();
        closeTab(id);
      });
      btn.appendChild(close);
      btn.addEventListener("click", () => activateTab(id));
      this.el.appendChild(btn);
    }
  }

  refreshLabel() { this.refresh(); }
}

const tabBar = new TabBar();

function addTab(data) {
  const container = document.createElement("div");
  container.className = "bm-tabcontent";
  container.style.display = "none";
  document.getElementById("main").appendChild(container);
  const tab = data.kind === "dir" ? new DirTab(container, data)
    : data.kind === "vc" ? new VcTab(container, data)
    : new FileTab(container, data);
  session.tabs.set(data.id, tab);
  session.order.push(data.id);
  tabBar.refresh();
  return tab;
}

const HINTS = {
  file: "Alt+↓/↑ change · Alt+←/→ push · Alt+Shift+←/→ pull · " +
        "Alt+Del delete · Ctrl+K/J conflict · Alt+M merge all · ⌘/Ctrl+S save",
  dir: "Enter/double-click compare · single-click toggle folder · Rescan",
  vc: "Enter/double-click compare vs repo · Commit · Revert · Rescan",
};

function activateTab(id) {
  session.active = id;
  for (const [tabId, tab] of session.tabs) {
    tab.container.style.display = tabId === id ? "" : "none";
  }
  const tab = session.tabs.get(id);
  setStatus(tab.status || "");
  document.getElementById("hints").textContent = HINTS[tab.kind] || "";
  tabBar.refresh();
  tab.activate();
}

function closeTab(id) {
  const tab = session.tabs.get(id);
  if (tab.kind === "file" && tab.dirty &&
      !confirm("Unsaved changes — close this tab?")) {
    return;
  }
  send({ type: "close_tab", tab: id });
  tab.container.remove();
  session.tabs.delete(id);
  session.order = session.order.filter((t) => t !== id);
  if (!session.order.length) {
    finish();
    return;
  }
  if (session.active === id) activateTab(session.order[0]);
  else tabBar.refresh();
}

function finish() {
  session.done = true;
  send({ type: "close" });
  const banner = document.getElementById("banner");
  banner.textContent = "session ended — you can close this tab";
  banner.style.display = "block";
}

// --- keymap (routed to the active tab) -----------------------------------------------

function activeFileTab() {
  const tab = session.tabs.get(session.active);
  return tab && tab.kind === "file" ? tab : null;
}

function meldKeymap() {
  const withTab = (fn) => () => {
    const tab = activeFileTab();
    if (tab) fn(tab);
    else bell();
    return true;
  };
  return [
    { key: "Alt-ArrowDown", run: withTab((t) => t.navChunk(1)) },
    { key: "Ctrl-d", run: withTab((t) => t.navChunk(1)) },
    { key: "Alt-ArrowUp", run: withTab((t) => t.navChunk(-1)) },
    { key: "Ctrl-e", run: withTab((t) => t.navChunk(-1)) },
    { key: "Alt-ArrowRight", run: withTab((t) => t.pushAction(1)) },
    { key: "Alt-ArrowLeft", run: withTab((t) => t.pushAction(-1)) },
    { key: "Shift-Alt-ArrowRight", run: withTab((t) => t.pullAction(-1)) },
    { key: "Shift-Alt-ArrowLeft", run: withTab((t) => t.pullAction(1)) },
    { key: "Alt-Delete", run: withTab((t) => t.deleteChunkAction()) },
    { key: "Ctrl-k", run: withTab((t) => t.navConflict(1)) },
    { key: "Ctrl-j", run: withTab((t) => t.navConflict(-1)) },
    { key: "Alt-m", run: withTab((t) => t.mergeAllAction()) },
    { key: "Mod-s", run: withTab((t) => t.saveFocused()) },
  ];
}

// --- websocket ------------------------------------------------------------------------

ws.onmessage = (event) => {
  const msg = JSON.parse(event.data);
  if (msg.type === "init") {
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
    const build = document.getElementById("build");
    if (build && msg.version) {
      build.textContent = `bmeld ${msg.version} · ${msg.build}`;
      build.title = `tmeld ${msg.version}, asset build ${msg.build}`;
    }
    for (const tabData of msg.tabs) addTab(tabData);
    document.getElementById("done").addEventListener("click", finish);
    window.addEventListener("resize", () => {
      const tab = activeFileTab();
      if (tab) tab.renderOverlays();
    });
    activateTab(session.order[0]);
    return;
  }
  if (msg.type === "tab_added") {
    const tab = addTab(msg.tab);
    tab.onChunks(msg.chunks);
    activateTab(msg.tab.id);
    return;
  }
  const tab = session.tabs.get(msg.tab);
  if (msg.type === "chunks" && tab) return tab.onChunks(msg);
  if (msg.type === "tree" && tab) return tab.onTree(msg);
  if (msg.type === "saved" && tab) {
    tab.markDirty(msg.pane, false);
    setStatus(`Saved ${msg.path}`);
    return;
  }
  if (msg.type === "merged" && tab) {
    const view = tab.panes[1].view;
    view.dispatch({
      changes: { from: 0, to: view.state.doc.length, insert: msg.text },
    });
    return;
  }
  if (msg.type === "vc_result" && tab) {
    setStatus(msg.ok ? (msg.detail || "done") : `error: ${msg.detail}`);
    if (msg.ok) send({ type: "scan", tab: msg.tab });
    return;
  }
  if (msg.type === "error") setStatus(`error: ${msg.message}`);
};

ws.onclose = () => {
  if (!session.done) {
    const banner = document.getElementById("banner");
    banner.textContent = "connection lost — reload to reconnect";
    banner.style.display = "block";
  }
};
