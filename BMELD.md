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
