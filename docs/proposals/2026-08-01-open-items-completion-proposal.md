# Proposal Dossier — Open Items Completion

## Vorschlag

Die Offline-Isolations- und Legacy-Fixes sollen im Fork-PR zur Review
bereitgestellt werden. Die Root-/Bot-Tests bleiben der lokale Qualitätsgate;
die vier Drittanbieter-Warnungen werden als Wartungsnotiz geführt.

## Nutzen

- Root- und Bot-Eigentumsgrenzen sind reproduzierbar.
- Host-Konfigurations- und `/app`-Leaks werden vor dem Testlauf verhindert.
- Native AGFS sowie zentrale Legacy-Verträge sind belegt.
- Optionales Gemini und eigenständige OpenClaw-/API-Harnesses verfälschen den
  Root-Status nicht.

## Nicht enthalten

Kein Provider-Live-Aufruf, kein OpenClaw-Service-Lauf, keine H1/H2-Promotion,
kein Default-Rollout des Responses-/Compaction-Modus und kein automatischer
Merge. Drittanbieter-Warnungen werden nicht durch lokale Vendor-Patches
verändert.

## Freigabeentscheidung

Offline: zur Review bereit. Live: HOLD bis zu einer getrennten Genehmigung und
Capability-/Benchmark-Evidenz. Promotion erfordert weiterhin die im
Responses-Dossier festgelegten Qualitäts-, Latenz-, Token-, Fehler- und
Cross-Chain-Kriterien.
