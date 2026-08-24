# Open-Item-Report: Security Hardening

**Stand:** 2026-07-27
**Branch:** `agent-workflow/20260727-security-hardening`
**Regel:** Ein Eintrag ist erst geschlossen, wenn der zugehörige Nachweis ausgeführt und dokumentiert wurde. Dieser Bericht enthält absichtlich genau drei High-, drei Medium- und drei Low-Einträge.

## High

### H-01 — Zeitlich begrenzte Scanner-Baseline: 74 Advisory-Ausnahmen

Die neue, fail-closed geprüfte Baseline `.github/security-audit-baseline.json` enthält 74 paketpfadgenaue Ausnahmebefunde und läuft am **2026-08-27** ab. Sie ist kein Risiko-Akzeptanzersatz: neue, verschobene, fehlende oder abgelaufene Befunde lassen den Verifier fehlschlagen. Bis zur Entfernung verbleibt ein High-Risiko, weil bekannte Advisories in mehreren Ökosystemen vorhanden sind.

**Nachweis / Wiederholung:**

```sh
jq 'length' .github/security-audit-baseline.json
python scripts/verify_security_audit_baseline.py --help
```

**Exit-Kriterium:** Alle 74 Einträge durch kompatible, getestete Updates entfernen oder vor Ablauf eine neue, explizit freigegebene Risikobewertung vornehmen. Keine Frist stillschweigend verlängern.

### H-02 — Starlette benötigt eine inkompatible Major-Anhebung

Die Python-Scanner-Baseline enthält mehrere `starlette@0.52.1`-Befunde. Der auditierte Fixpfad erfordert eine neue Major-Linie und wurde deshalb nicht gegen die vorhandene FastAPI-Integration erzwungen. Ein Major-Update ohne Kompatibilitätsmatrix würde die Anforderung „nur kompatible Updates“ verletzen.

**Nachweis / Wiederholung:**

```sh
uv export --locked --no-hashes --format requirements-txt -o .security-requirements.txt
uv tool run --from pip-audit==2.10.0 pip-audit -r .security-requirements.txt --format json
```

**Exit-Kriterium:** Separate FastAPI/Starlette-Kompatibilitäts- und Migrationsphase, vollständige Server- und OAuth-Regressionen, dann nur noch geprüften Lockfile-Diff übernehmen.

### H-03 — Rust-Transitivpfade: gix, PyO3, RSA und ältere rustls-webpki-Linie

Kompatible Patch-Updates wurden nur dort übernommen, wo Cargo sie ohne Major-Wechsel auflösen konnte. Die verbleibenden Pfade über `gix-*`, `pyo3@0.27`, `rsa@0.9` (teilweise ohne verfügbaren Patch) und die ältere `rustls-webpki`-Linie brauchen eine eigenständige Abhängigkeits-/API-Migration. Insbesondere PyO3 verlangt eine große Versionsanhebung; ein blindes `cargo update` ist nicht zulässig.

**Nachweis / Wiederholung:**

```sh
cargo audit --json > .security-cargo-audit.json
cargo tree -i pyo3@0.27.2
cargo tree -i rsa@0.9.10
```

**Exit-Kriterium:** Pfadbesitzer bestimmen, Upstream-Kompatibilität validieren, Migrations-PR mit Rust-Testmatrix und neuem `cargo audit` erstellen.

## Medium

### M-01 — Web-Studio: shadcn-/transitive Dependency-Kette nicht kompatibel aktualisiert

Die Restbaseline enthält transitive Web-Studio-Befunde (unter anderem aus der shadcn-/Hono-/Mermaid-Kette). Ein Upgrade würde mehrere gekoppelte Paketlinien oder einen Major-Wechsel verlangen. Es wurde nicht in diese Sicherheits-Patchserie gemischt.

**Nächster Schritt:** Einen dedizierten Frontend-Abhängigkeitsplan mit Lockfile-Diff, Vitest, Lint, Build und UI-Smoke-Test erstellen.

### M-02 — Bot: `shell-quote` und `lodash-es` nur über inkompatible Elternpfade erreichbar

Die Bot-Befunde bleiben paketpfadgenau in der Baseline, weil verfügbare Updates nur über die Elternkette (einschließlich `@anthropic-ai/sandbox-runtime`) und nicht als risikoarmer kompatibler Patch erreichbar waren.

**Nächster Schritt:** Laufzeitverwendung und Eingabegrenzen des Bots prüfen, aktualisierte Elternversion in separater Sandbox-/Bot-Testphase testen und den Baseline-Eintrag danach entfernen.

### M-03 — Lokaler `pip-audit`-Subprozess endete mit `SIGABRT`

Die vorgesehenen Audit-Kommandos wurden definiert, aber der lokale `pip-audit`-Subprozess beendete sich mit `SIGABRT`. Das ist kein grünes Audit-Ergebnis und wird nicht als bestanden gewertet.

**Nächster Schritt:** In einer sauberen Python-3.11-Umgebung (oder in der neu ergänzten GitHub-Actions-Umgebung) das Export-/Audit-Kommando unverändert wiederholen und die JSON-Ausgabe mit dem Baseline-Verifier vergleichen.

## Low

### L-01 — Vollständige lokale Python-Suite braucht eine gültige `ov.conf`

Die vollständige Suite konnte lokal nicht in einer realen Konfiguration gestartet werden, weil keine passende `ov.conf` vorlag. Fokussierte Tests können die fehlende Betreiberkonfiguration nicht ersetzen.

**Nächster Schritt:** Eine nicht-sekrete Testkonfiguration bereitstellen und ausführen:

```sh
uv run pytest tests/server/test_api_webdav.py tests/server/test_public_url.py tests/session/memory/test_graph_view.py
```

### L-02 — Kein Browser-E2E und kein Serverstart belegt

Es gibt keinen erfolgreich nachgewiesenen Browser-E2E-Lauf und keinen Start einer vollständigen Server-/Studio-Umgebung. Daher sind die DOM- und API-Tests kein Produktions- oder Proxy-Beweis.

**Nächster Schritt:** Nach Bereitstellung der Testkonfiguration einen isolierten Server und Web-Studio starten, gespeichertes Markdown mit erlaubten und gesperrten Schemas prüfen und den Testprozess anschließend normal beenden. Kein Neustart eines Rechners oder Servers ist Teil dieses Schritts.

### L-03 — `agy`-Review war im Headless-Modus nicht berechtigt

Der geforderte Antigravity-Review wurde versucht, scheiterte aber an einer Headless-Berechtigungsverweigerung. Die Dossiers dokumentieren diesen fehlenden externen Review; die lokale Review- und Testpflicht bleibt bestehen.

**Nächster Schritt:** Mit interaktiv autorisiertem `agy` denselben selbständigen Review-Prompt gegen ARD/TRD/PD/ID/TD/PPD laufen lassen und angenommene oder abgelehnte Empfehlungen in den jeweiligen Dossiers nachtragen.

## Stop-Regeln

- Keine Ausnahme nach dem 2026-08-27 akzeptieren.
- Kein Major-Upgrade zur Schließung eines Befunds ohne isolierte Kompatibilitäts- und Regressionsergebnisse.
- Keine öffentliche Bereitstellung als geprüft melden, solange L-01 und L-02 offen sind.
- Keine Konfiguration, Schlüssel oder Produktionsdaten in Testartefakte übernehmen.
