# Open Items — Open Items Completion (2026-08-01)

## High (3)

1. **H1 — Codex Capability:** Exakten Codex-Endpunkt mit OAuth,
   `context_management`, Compaction-Items, Replay und `store=false` prüfen;
   bis zum Evidence-Log HOLD.
2. **H2 — Responses Benchmark:** 20 reale und 10 synthetische Szenarien mit
   Wiederholungen, Token-/Latenz-/Fehler-/Kontinuitätsmetriken ausführen; bis
   zur separaten Live-Freigabe HOLD.
3. **H3 — OpenClaw P0/Service:** Echten Service-Health-/MCP-Handshake und
   read-only P0-Lauf mit Stop-/Rollback-Plan ausführen; bis dahin HOLD.

## Medium (3)

1. **M1 — Drittanbieter-Warnungen (Kompatibilität offline geschlossen):** `lark-oapi 1.7.1`,
   `uvicorn 0.52.1` und `websockets 15.0.1` sind kompatibel gelockt; der
   OpenViking-/Bot-Pfad nutzt SansIO. Zwei upstream Lark-Warnungen bleiben
   sichtbar und ungefiltert als Wartungsrest; der genaue Ledger steht in
   `docs/dossiers/2026-08-02-live-gates-and-lark-warning-ledger.md`.
2. **M2 — Fork-PR-Review:** Commit/Push, CI und Review im Fork abschließen;
   der PR darf nicht automatisch gemergt werden.
3. **M3 — Live-Evidence-Paket:** Für H1/H2/OpenClaw disposable Config,
   Credential-Fingerprint, Kosten-/TTL-Limits und sanitisiertes Ergebnisformat
   vorbereiten.

## Low (3)

1. **L1 — Parallel-Evidence:** Kontrollierten xdist-Lauf mit getrennten
   Workspaces wiederholen und die Artefaktpfade archivieren.
2. **L2 — Typpräzision:** Stabile Fixture-/Config-Dicts bei einer separaten
   Typisierungsrunde in TypedDict/Mapping überführen.
3. **L3 — Wartungsinventar:** Drittanbieter-Deprecations, optionale Extras und
   ihre Upgrade-Empfehlungen in einem CI-Wartungsticket nachhalten.
