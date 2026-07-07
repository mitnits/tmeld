# bmeld — Meld in the browser (design)

`bmeld local.py base.py remote.py` starts a local server, prints a
clickable URL, and serves a Meld-faithful merge UI to the browser. The
process stays alive and exits with the git mergetool contract (0 iff
the merged file was saved). Same repo as tmeld: the diff engine,
Comparison model, dircompare/VC backends, and palette are shared.

Motivating measurement (spikes/pixelbench.py, 2026-07-07): rendering
pixels remotely costs RTT per interaction (~111 ms input-to-photon on
a real WAN link vs 6.7 ms locally). Conclusion: render locally in the
browser, move only data over the wire.

## Architecture

**Thick client, thin truth.** CodeMirror 6 owns the text buffers —
typing and chunk actions are local and instant. The Python server owns
correctness: it runs the vendored Meld engine on debounced buffer
snapshots and pushes back the chunk model. Meld parity lives entirely
in the engine (identical bytes); the browser only renders.

- Server: aiohttp (the only new dependency; `pip install tmeld[web]`,
  console script `bmeld`, CLI mirrors tmeld's). One process: static
  assets + one WebSocket per session.
- Client: CodeMirror 6 editors (decorations = our line_tags /
  inline_ranges), SVG for linkmap connectors (real beziers, free AA —
  no graphics-protocol probing), SVG chunkmap, clickable gutter
  arrows, tabs. Sync scroll = JS port of scroll.py interpolate_line.
  Keybindings identical to tmeld (preventDefault: Alt+Left is browser
  back, Ctrl+S is save-page). Exact palette hexes shipped in init.
  Bundle: esbuild -> single committed self-contained asset (no CDN;
  works airgapped).
- Chunk actions apply client-side (JS port of _replace_lines against
  known chunk ranges) for zero-latency feedback; the following rediff
  round-trip refreshes decorations, like tmeld's debounce.

## Protocol (WebSocket, JSON)

Server -> client:
- `init`    {files, labels, readonly, texts, palette, output, version}
- `chunks`  {version, line_tags[], inline_ranges[], action_starts[],
             conflicts, diff_count}   — Comparison outputs, serialized
- `saved`   {pane, ok}
- `error`   {message}

Client -> server:
- `buffers` {version, texts}   (debounced 250 ms) -> rediff -> chunks
- `save`    {pane, text}
- `close`   {}                 -> resolve exit status, shut down

Version counter discards stale chunk responses. Reconnect with the
same token resumes (reload-safe); state of record = files on disk +
server-side saved flags.

## Transport tiers (pinned design, CLAUDE.md)

1. Direct: bind 127.0.0.1, URL = /t/<secrets.token_urlsafe>, printed
   with OSC 8 hyperlink; auto webbrowser.open when not under SSH.
2. SSH: SSH_CONNECTION detected -> print `ssh -O forward -L P:localhost:P host`
   hint. Ergonomics: `--port 8731` + a one-time LocalForward line in
   ~/.ssh/config makes every future link Just Work. (VS Code terminals
   auto-forward printed localhost URLs.)
3. Relay (later, opt-in): outbound wss, E2E key in URL fragment
   (relay sees ciphertext only).

## Lifecycle / mergetool contract

Process exits when the client sends `close` (or Ctrl+C). Exit 0 iff
middle pane saved (3-way), like tmeld. Tab closed without notice:
beforeunload beacon, else WS-disconnect grace timer (~60 s) -> exit 1.

## Security

127.0.0.1 only; unguessable token gates HTTP and WS; CSP; no
filesystem API beyond the argv paths; self-contained assets.

## Phases

- B0  skeleton: server, token, URL print/open, exit plumbing, tests.
- B1  read-only 2-way at full visual parity (fills, inline, SVG
      connectors, chunkmap, sync scroll). Screenshot-compare vs tmeld.
- B2  editing, chunk actions, save, mergetool contract.
- B3  3-way, conflicts, merge-all, .gitconfig docs.
- B4  tabs/--diff, folder comparison, VC view (engines exist; trees
      are easy in HTML).
- B5  transport tier 3 (relay), polish.

Testing: aiohttp test client for protocol/session/exit-code tests
(pytest, like tests/test_vcview.py style); browser E2E via Playwright
optional later. Related: awrit-style in-terminal rendering composes
with this (bmeld on localhost + kitty-graphics browser) — see the
pinned note in CLAUDE.md.

## Appendix: implementation nitty-gritty

### Client toolkit

Vanilla TypeScript + CodeMirror 6, no framework (three editors + SVG
overlays + a tab bar is imperative DOM; React/Vue would bloat the
airgapped bundle for nothing). Pinned packages:

- `@codemirror/state`, `@codemirror/view` — EditorView, Decoration,
  StateField/StateEffect, ViewPlugin, keymap facet
- `@codemirror/commands` — defaultKeymap, history (undo/redo per pane
  for free, replacing tmeld's TextArea history)
- later: `@codemirror/language` + Lezer grammars for syntax under
  chunk fills (Phase-9 parity item, easier here than in the TUI)
- devDependency: `esbuild`. Build: `esbuild src/main.ts --bundle
  --minify --outfile=../tmeld/web/static/bmeld.js` (+ one CSS file).
  Bundle ≈ 300 KB min. Node is needed only to develop the client; the
  built bundle is committed so `pip install tmeld[web]` needs no JS
  toolchain. A CI step rebuilds and diffs to catch stale bundles.

Layout: CSS grid `[editor] 6px-gutter [editor] (6px-gutter [editor])
20px-chunkmap`; each gutter cell hosts an `<svg>` (connectors) with
absolutely-positioned arrow `<button>`s; tab bar is a flex row of
divs with the ✕ pattern from tmeld.

### Rendering the chunk model in CM6

- Line fills: `Decoration.line({class: "chunk-insert"})` etc. from
  `line_tags`; colors are CSS variables set at init from the palette
  JSON, so exact Meld hexes and live theme switching.
- Inline highlights: `Decoration.mark(from, to)` with (line, col) ->
  pos via `doc.line(n + 1).from + col`.
- Current-chunk emphasis: extra line class; cursor -> chunk via a JS
  locate_chunk (binary search over chunk ranges, port of the differ
  helper's semantics client-side against the last chunks payload).
- Chunk boundary lines: border-top/bottom on first/last chunk lines —
  the browser finally does Meld's saturated 1px boundaries properly
  (open TUI parity item falls out for free here).
- Decorations live in a StateField updated by a StateEffect dispatched
  when a `chunks` message lands; stale versions are dropped.

### Connectors (SVG)

Recompute on scroll/resize/chunks. For each visible pair chunk, pixel
ys come from `view.lineBlockAt(doc.line(n).from).top` minus
`scrollDOM.scrollTop` plus pane offset; the path is upstream
linkmap.py's exact shape as one SVG path:
`M -0.5,f0 C W/2,f0 W/2,t0 W+0.5,t0 L W+0.5,t1 C W/2,t1 W/2,f1
-0.5,f1 Z`, fill = chunk fill (emphasis blend for the cursor chunk),
stroke = chunk line color, stroke-width 1. Free anti-aliasing; no
probing, no encoders. ChunkMap: an SVG column of rects scaled by
total lines + a translucent viewport lens (palette overlay alpha);
click-to-jump.

### Sync scroll and editing loop

- Sync scroll: 'scroll' listeners on each `scrollDOM`; the JS port of
  scroll.py (calc_syncpoint, interpolate_line, offset-for-line, and
  the 3-way re-master-on-middle cascade). Echo suppression: tag the
  programmatic scroll target and swallow the matching event, same as
  pane.sync_scroll_to.
- Edits: `EditorView.updateListener` marks the pane dirty and arms a
  250 ms timer; firing sends `buffers {version, texts}`; the `chunks`
  reply re-decorates. Chunk actions (push/pull/delete/copy/merge-all)
  are client-side text edits computed from current chunk ranges (JS
  port of _replace_lines incl. the EOF-newline splice), so feedback is
  instant; the rediff catches up like tmeld's debounce.
- Readonly panes: `EditorState.readOnly` + `EditorView.editable`.
- Keymap: CM keymap facet, tmeld's exact bindings; preventDefault on
  Alt+Left/Right (history nav) and Ctrl+S (save page). Alt+M, Ctrl+K/J
  etc. unchanged.

### Server nitty-gritty

- aiohttp web.Application; routes: `GET /t/{token}` -> index.html,
  `GET /assets/*` -> static, `GET /ws/{token}` -> WebSocket. Any token
  mismatch -> 404 (no OPTIONS/CORS surface; same-origin only; CSP
  `default-src 'self'`).
- `tmeld/web/protocol.py`: `init_payload(comparison, palette)` and
  `chunks_payload(comparison)` — pure serialization of the existing
  Comparison methods (line_tags / inline_ranges / action_starts /
  differ.conflicts). Parity is structural: same engine objects tmeld
  renders from.
- Session: owns Comparison + saved flags; `save` writes via
  comparison.save (trailing-newline behavior preserved); `close` (or
  WS-disconnect grace expiry) sets an asyncio.Event the CLI awaits
  before returning exit_status.
- CLI: argparse mirroring tmeld (files/-o/--theme), plus --port
  (default 0 = ephemeral; fixed port recommended for the ssh -L
  workflow), --no-open, --grace SECONDS.
- Port print: OSC 8 hyperlink wrapping the URL for click-through in
  terminals.

### Tests

- Protocol: pytest + aiohttp test client; reuse test_threeway.py
  fixtures — connect a WS client, send buffers, assert chunk payloads
  (tags/ranges/conflicts) and exit codes for save/close sequences.
- Bundle freshness: CI rebuilds the JS and fails on diff.
- Browser E2E: Playwright smoke (open URL, assert fills/conflict
  colors, push a chunk, save, exit code) — optional job, can land
  after B2.
