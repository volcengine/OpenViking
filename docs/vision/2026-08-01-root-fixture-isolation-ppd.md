# PPD — Root-Fixture-Isolation

## Vision

Die Root-Testgrenze soll deterministisch, offline und unabhängig von einer
lokalen Host- oder Containerkonfiguration sammeln und laufen. Ein Embedded-
Client darf den Workspace vor der Pydantic-/`StorageConfig`-Validierung
ersetzen. Jede Root-Fixture besitzt ihren eigenen `tmp_path`, eine sichere
per-Test-`ov.conf` für native AGFS-Auflösung und lokale Fake-Provider. Kein
Credential, kein Provider-Endpunkt und kein `/app`-Pfad darf durch diesen
Offline-Pfad in den Testprozess gelangen.

Dieses PPD ist die langfristige Entscheidungsgrundlage; der aktuelle Change
behebt ausschließlich den `/app`-Ordering-Blocker und die Root-Fixture-Grenze.
Native Bindings, OpenClaw-/Service-Läufe, H1/H2 und Provider-Live bleiben
bewusst außerhalb des aktuellen Rollouts.

## Entscheidungsrahmen

| Kriterium | Ziel | Nachweis |
|---|---|---|
| Isolation | Kein Host-/`/app`- oder Repository-Workspace | Sentinel-, `Path.mkdir`- und `tmp_path`-Tests |
| Determinismus | Keine externe Modell-/Embedding-I/O | Fake-Embedder/VLM und endpointfreie `ov.conf` |
| Kompatibilität | Legacy-Aufrufer ohne Override unverändert | 29 Config-Legacy-Tests |
| Transparenz | Nicht erfüllte Native-/Live-Gates bleiben sichtbar | TD/STP/Open-Items, fail-closed |
| Rückrollbarkeit | Kleine, gezielte Source-/Teständerung | Diff- und SHA-Manifest vor Commit |

## Zukunftsoptionen und Vorschläge

### PPD-01 — Früher optionaler Workspace-Override (umgesetzt)

**Rationale.** Der Pfad muss vor `StorageConfig.resolve_paths()` ersetzt werden;
ein späteres Überschreiben kann den Hostpfad bereits angelegt haben.

**Vorteile:**

- behebt die konkrete `/app`-Ursache an der richtigen Reihenfolge;
- lässt bestehende No-Override-Aufrufer unverändert;
- ist mit einem kleinen, synchronen API-Parameter testbar.

**Nachteile:**

- ein bereits geladener Singleton wird nicht automatisch neu geladen;
- direkte Konstruktoren müssen weiterhin ihre Fixture-Grenze dokumentieren;
- der Parameter erweitert einen globalen Singleton-Vertrag.

### PPD-02 — Per-Test-Datei plus `OPENVIKING_CONFIG_FILE` (umgesetzt)

**Rationale.** Native AGFS liest den Pfad über die Umgebungsvariable; ein
bereinigtes Dict allein schützt diesen nativen Pfad nicht.

**Vorteile:**

- identischer sicherer Pfad für Python- und native Auflösung;
- endpoint- und credentialfreie Datei ist leicht zu inspizieren;
- `tmp_path` macht parallele Tests voneinander unabhängig.

**Nachteile:**

- die Root-Conftest setzt die Variable bereits beim Import global für den
  pytest-Prozess;
- Tests mit eigener Config müssen ihre Zustandsgrenze explizit markieren;
- native Bindings bleiben ohne installierte Bibliothek unbewiesen.

### PPD-03 — Deterministische lokale Provider-Fakes (umgesetzt)

**Rationale.** Offline-Tests dürfen nicht versehentlich LiteLLM, OAuth oder
einen entfernten VLM ansprechen.

**Vorteile:**

- reproduzierbare Dimension und Antwort;
- keine Secrets oder Netzwerkrouten nötig;
- schnell und unabhängig von Modellverfügbarkeit.

**Nachteile:**

- echte Provider-Kompatibilität wird nicht abgedeckt;
- Fake-Verträge können reale Fehler nicht ersetzen;
- Patch-Reihenfolge muss bei neuen Fixtures geprüft werden.

### PPD-04 — Expliziter Offline-Collection-Job (nächster CI-Schritt)

**Rationale.** Root-Collection und eigenständige Harnesses müssen getrennt
ausgewertet werden; optionale Abhängigkeiten dürfen keinen stillen PASS
erzeugen.

**Vorteile:**

- kanonische venv und Env-Clearing werden reproduzierbar;
- PASS/FAIL/HOLD-Artefakte werden maschinenlesbar;
- Harness-Grenzen bleiben sichtbar.

**Nachteile:**

- zusätzliche CI-Zeit und Pflege;
- Subprojekt-Abhängigkeiten müssen versioniert werden;
- ein Offline-Job beweist keine Live-Funktion.

### PPD-05 — Native AGFS-/Lifecycle-Gate (separater Hold)

**Rationale.** Erst eine verifizierte `ragfs_python`-Installation kann den
Lifecycle hinter der aktuellen Importgrenze beurteilen.

**Vorteile:**

- beweist die native Pfadauflösung statt nur Python-Semantik;
- deckt reale Client-/Service-Initialisierung ab;
- lässt sich gegen das sichere `ov.conf` reproduzieren.

**Nachteile:**

- Build-/Plattformabhängigkeit und längere Laufzeit;
- kann native Toolchain oder Servicezugriff benötigen;
- braucht eine separate Freigabe und Rollback-/Stop-Prozedur.

### PPD-06 — Kontrollierter xdist-Nachweis (separater Hold)

**Rationale.** `tmp_path` ist pro Test eindeutig, muss aber in der nativen
Umgebung mit parallelen Workern belegt werden.

**Vorteile:**

- entdeckt globale Singleton-/Cleanup-Übertritte;
- liefert belastbare Parallelitäts- und Retentionsnachweise;
- stärkt das CI-Gate für lange Root-Suites.

**Nachteile:**

- parallelisierte native Tests können zusätzliche Ressourcen benötigen;
- globale Import-Hooks bleiben ein mögliches Risiko;
- ohne native Umgebung ist der Nachweis aktuell nicht zulässig.

## Risiken und Gegenmaßnahmen

| Risiko | Gegenmaßnahme | Status |
|---|---|---|
| Host-`/app` wird vor Override validiert | Override im Datei-Decode, Sentinel-/Mkdir-Guard | PASS |
| Provider-I/O oder Secret-Leakage | endpointfreie Datei, Env-Clearing, lokale Fakes, Log-Review | PASS offline |
| Singleton-Zustandsübertritt | function-scoped Reset in `finally`, Legacy-Resync-Test | PASS offline |
| Native AGFS liest eine andere Datei | `OPENVIKING_CONFIG_FILE` auf sichere per-Test-Datei setzen | PASS als Python-Vertrag; Native HOLD |
| Optionale Harnesses verfälschen Root-Gate | separate Collection-/Harness-Grenzen, fail-closed | FAIL/HOLD dokumentiert |
| Live-Gates werden versehentlich gestartet | explizite Freigabe und Stop-Regeln im STP | HOLD |

## Delta und QWF

| Phase | Delta | QWF/Gate |
|---|---|---|
| P0 | Worktree, ARD/TRD, Host-RED sichern | Identität und schmutziger Main unverändert |
| P1 | Früher Override implementieren | `/app`-Sentinel und malformed-config PASS |
| P2 | Root-Fixture isolieren | Fake-Provider, sichere Datei, 40 fokussierte PASS |
| P3 | Legacy-/Boundary-Regressionslauf | 29 Legacy + 3 Boundary PASS; Warnungen sichtbar |
| P4 | Review und Dokumentation | Simulation 96,4 %, Evidence-Matrix, STP |
| P5 | Fork-Veröffentlichung | gezielter Commit, Push, Draft-PR; kein Merge |
| P6 | Separate Freigabe | Native, OpenClaw, H1/H2, Provider-live erst nach Approval |

## Promotionskriterien

Die Offline-Isolation gilt als implementiert, wenn der fokussierte Lauf 40/40
passiert, keine `/app`- oder Host-Sentinel-Nebenwirkung auftritt,
`git diff --check` grün ist und Root-Collection-/Native-/Live-Grenzen als
FAIL/HOLD ausgewiesen bleiben. Eine Promotion zum allgemeinen Root- oder
Live-Standard erfordert zusätzlich PPD-04 bis PPD-06 und die separaten
H1/H2-/Provider-Gates; dieser Change aktiviert sie nicht.
