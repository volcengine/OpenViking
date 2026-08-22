# Planning Document — Lark/WebSockets Compatibility

**Stand:** 2026-08-02
**Modus:** `$tccode` `thorough`/`critical`, Agent-Workflow-v4 innerhalb der
Phasen
**CI:** ausschließlich `/Volumes/ExtremePro/projects/local-ci-gate`

## QWF

| Rang | Paket | Risiko | Nachweis |
|---:|---|---|---|
| 1 | reproduzierbare Paketmatrix | hoch | `uv.lock` und Versionstest |
| 2 | expliziter SansIO-Serverpfad | hoch | Bootstrap-Regression |
| 3 | Lark-Import-Warnungstest | mittel | kein WebSockets-Legacy-Hinweis |
| 4 | lokales Gate | hoch | fünf projektweite Checks, einschließlich des neuen Kompatibilitäts-Checks |
| 5 | Dokumentation/Rest-Risiko | mittel | ARD/TRD/ID/TD/STP/Diary |

## Simulation

| Kriterium | Prognose |
|---|---:|
| Technische Machbarkeit | 98 % |
| Vertragskorrektheit | 97 % |
| Scope-Vollständigkeit | 96 % |
| Regressionsrisiko | 95 % |
| Wartbarkeit | 96 % |

Die Simulation überschreitet den Workflow-Schwellenwert von 95 %. Die
upstream-Lark-Warnungen bleiben ein ausdrücklich markiertes Rest-Risiko statt
einer stillen Filterung.

## Freigaberegel

Die Änderung bleibt offline/opt-in. H1/H2, OpenClaw-P0 und Provider-Live-Gates
werden nicht gestartet.
