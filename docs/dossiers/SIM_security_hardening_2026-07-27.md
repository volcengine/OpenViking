# Simulationsbericht: Security Hardening 2026-07-27

## Ergebnis

Die erste Simulation verweigerte den Start (82 %), weil der Resolver-/OAuth-Vertrag, ausführbare Graph-Prüfung, echte Stream-Semantik, Advisory-Baseline und sichere Beispiele nicht vollständig spezifiziert waren. Diese Punkte wurden in ARD/TRD/ID/TD ergänzt und in der zweiten Iteration erneut geprüft.

| Kategorie | Ergebnis |
|---|---:|
| Korrektheit | 97 % |
| Integration | 96 % |
| Sicherheit | 98 % |
| Testbarkeit | 97 % |
| Performance | 97 % |
| Wartbarkeit | 97 % |
| Beobachtbarkeit | 96 % |
| Rollback | 99 % |
| Gesamt | 97 % |

**Gate:** bestanden. Jede Kategorie und das Gesamtergebnis liegen über dem im `tccode`-Workflow verlangten 95-%-Schwellwert.

## Verbindliche Prüfhinweise aus der Simulation

- Die Graph-Prüfung führt den generierten JavaScript-Pfad mit einem Node-VM-Harness aus; reine Template-String-Assertions reichen nicht.
- Der Public-Origin-Resolver wird vor dem OAuth-Registrierungs-`try/except` validiert; eine ungültige Cross-Konfiguration darf nicht als „Skipping OAuth router registration“ verschwinden.
- Der Advisory-Verifier arbeitet mit einer feldgenauen, ablaufenden Baseline und lehnt neue oder verschobene Befunde ab.
- Die WebDAV-Prüfung kontrolliert den ASGI-Receive-Stream und beweist, dass kein Schreib-/Summarize-Pfad nach dem Limit erreicht wird.

## Review-Tool-Grenze

`agy` wurde für Plan- und ID-Review aufgerufen, lieferte in zwei Versuchen keine aufgabenspezifische Review-Antwort. Der dritte, präzise Aufruf endete fail-closed mit `jetski: ... command permission ... auto-denied`; ein Ausweichen mit `--dangerously-skip-permissions` wurde nicht vorgenommen. Die unabhängigen Architecture-, Security- und Simulation-Agenten wurden stattdessen als nachvollziehbare Reviews eingesetzt. Dieser Tool-Blocker bleibt im Abschlussbericht sichtbar.
