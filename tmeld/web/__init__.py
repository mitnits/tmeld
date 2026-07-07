"""bmeld: Meld in the browser, served by a local tmeld process.

Design: BMELD.md. Thick client, thin truth — the browser (CodeMirror)
owns the buffers; this server runs the vendored Meld engine on
debounced snapshots and is the only thing that touches the disk.
"""
