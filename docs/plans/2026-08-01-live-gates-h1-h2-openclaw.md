# Plan — OpenClaw-P0, Codex-H1 und Codex-H2 (Live-Phase)

Status: **HOLD / NOT RUN**
Stand: 2026-08-02
Ziel-Repository: `manni07/OpenViking`

Der aktuelle Evidence-Ledger steht in
[`docs/dossiers/2026-08-02-live-gates-and-lark-warning-ledger.md`](../dossiers/2026-08-02-live-gates-and-lark-warning-ledger.md).
Die Offline-WebSocket-Kompatibilität ist abgeschlossen; die beiden
Lark-Upstream-Warnungen bleiben separat dokumentiert und sind kein Anlass für
einen lokalen Filter oder einen `site-packages`-Patch.

## Zweck und harte Grenzen

Dieses Dokument beschreibt die separat freizugebende Live-Phase. Es aktiviert
keinen Provider, startet keinen Dienst und verwendet weder API-Key noch OAuth-
Credential ohne eine neue, explizite Lauf-Freigabe. Die Offline-Suite und die
Native-/Lifecycle-Nachweise sind davon unabhängig.

Die Live-Phase darf nur mit einem exakt benannten, temporären Arbeitsverzeichnis,
einem einzelnen freigegebenen `CodexVLM`-Pilotobjekt und einem einzelnen
Credential beginnen. Kein Account-/Provider-Failover, keine Conversations,
keine `previous_response_id` und kein automatisches Überspringen eines
fehlenden Capability-Features.

## Freigabe-Eingang (vor jedem Netzwerkzugriff)

1. Auftraggeber bestätigt schriftlich: Live-Phase starten, erlaubter Endpunkt,
   Modell, Zeitfenster, Kostenlimit und maximale Request-/Turn-Zahl.
2. OAuth- oder API-Key-Modus wird festgelegt. Für OAuth ist ausschließlich der
   bereits freigegebene HTTPS-Codex-Origin zulässig; benutzerdefinierte Origins
   und Redirects sind verboten. Tokenwerte werden nie in Logs, Reports oder
   Artefakten ausgegeben.
3. Der exakte Prozess/Container für OpenClaw wird identifiziert. Ein Neustart
   ist nur für diesen Prozess und nur nach Bestätigung zulässig; Rechner- oder
   fremder Dienst-Neustart bleibt ausgeschlossen.
4. Es wird ein frischer, privater, löschbarer State-/Artefaktpfad mit
   Eigentümer- und Modusprüfung eingerichtet. Vorheriger Zustand wird nicht
   überschrieben.
5. `git status`, Branch, Commit und SHA-256-Manifeste werden eingefroren.

Ohne diese Felder gibt es keinen Probe-Request. Insbesondere reichen ein
vorhandenes OAuth-Token, ein lokaler Health-Endpunkt oder ein erfolgreicher
Offline-Test nicht als implizite Freigabe. Für den H1-Pilot muss die
Approval-Datei außerdem vor dem Credential-Resolver und vor jeder Client- oder
Netzwerk-Factory strikt schema-validiert werden; unbekannte oder fehlende
Felder schlagen fail-closed fehl.

## H1 — Capability-Probe

### Vorbereitung

- Gegen den exakt verwendeten Codex-Endpunkt wird ein einzelner Probe-Request
  mit `store=false` und der vorgesehenen `responses_compact_threshold`
  ausgeführt.
- Geprüft werden ausschließlich die tatsächlich benötigten Fähigkeiten:
  `context_management`, Compaction-Items, vollständige `response.output`-
  Weitergabe (Reasoning/Tool/Compaction) und Replay des nächsten Turn-Deltas.
- Die Probe verwendet keine produktiven Conversations und keine
  `previous_response_id`.

### PASS-Kriterien

- Endpunkt akzeptiert alle benötigten Felder ohne stillen Fallback.
- `response.completed` ist eindeutig korrelierbar; Teilstream, Timeout,
  Cancellation und HTTP-Fehler veröffentlichen keinen neuen Zustand.
- Neuester Compaction-Item beschneidet ausschließlich den davor liegenden
  Kontext; spätere Items bleiben byte-/item-getreu erhalten.
- State-Grenzen (Bytes, Items, Turns, Bilder, Tool-Ausgaben, TTL) und
  Credential-/Generation-Bindung werden vor dem Netzwerkzugriff geprüft.

### HOLD-Kriterien

Unsupported-Feature, abweichender Origin, Redirect, fehlendes `store=false`,
unklare Replay-Semantik, Leak in Log/Trace oder irgendein stiller Fallback.

## H2 — kontrollierter Benchmark

H2 startet erst nach H1-PASS und einer erneuten Freigabe. Die Matrix bleibt
identisch und reproduzierbar:

- mindestens 20 sanitierte reale Langsitzungen;
- mindestens 10 synthetische Multi-Turn-/Tool-Szenarien;
- nichtdeterministische Szenarien dreimal;
- Baseline `206720`, `scope=total` gegen die gehärteten Hooks und die Kandidaten
  `206720/total`, `200000/total`, `200000/body_after_prefix`;
- keine 175k-Variante ohne belegten Bedarf.

Erfasst werden Kontinuität, Output-Tokens, p95-Latenz, Fehlerrate,
Compaction-Häufigkeit, Hook-Laufzeit, State-Größe und Cross-Chain-Leaks.

### Promotion-Gate

Der Zustand bleibt opt-in, sofern nicht alle Kriterien erfüllt sind: keine
Qualitäts- oder kritische Szenarioeinbuße, mindestens 20 Prozent weniger
mediane Output-Tokens, p95 höchstens 10 Prozent schlechter, keine höhere
Fehlerrate, null Cross-Chain-Leaks und 100 Prozent der kritischen Security-,
Kontinuitäts- und Legacy-Tests. Bei einem einzigen Fehlkriterium erfolgt kein
Default-Rollout.

## OpenClaw-P0-/Service-Lauf

1. Vorab aktuellen Status, Port/Origin, PID/Container und Health read-only
   erfassen; kein altes Upgrade-/Reset-/Pkill-Skript verwenden.
2. Vor dem Handshake eine disposable OpenClaw-Home-/Config- und
   OpenViking-Workspace-Bindung belegen. Host-Home, feste `/app`-/1933-/18789-
   Annahmen und der nicht versionierte Harness-`settings.py`-Pfad dürfen nicht
   verwendet werden.
3. Mit temporären Settings einen echten MCP-Handshake und genau einen
   read-only Tool-Aufruf ausführen. Ein Health-Endpunkt allein ist kein
   Handshake-Nachweis.
4. P0-Harness im eigenen Environment ausführen; Nachrichten, Responses,
   Secrets und Prompt-Inhalte bleiben aus Logs/Artefakten redigiert und
   begrenzt. Der aktuelle Harness ist mutierend, solange diese Isolation nicht
   bewiesen ist.
5. Bei 503 der Embedding-Abhängigkeit, fehlendem Handshake, stale Harness,
   rohen Sentinel-Secrets oder ungeklärter Prozessidentität sofort HOLD. Kein
   automatischer Restart.

## Beweispaket und Stop-Regeln

Das Live-Beweispaket enthält nur sanitierte JSON-/Markdown-Berichte:

- Start-/Endzeit, Commit, Endpunktkennung ohne Token, Modell und Modus;
- H1-Probeantworten/Capability-Matrix;
- H2-Rohmetriken, Median/p95 und Vergleichsmatrix;
- OpenClaw-Handshake-/P0-Ergebnis;
- SHA-256-Manifeste, Testkommandos und PASS/FAIL/HOLD/NOT_RUN-Ledger.

STOP bei Credential- oder Prompt-Leak, unerwarteter Speicherung, fremdem
Prozess, fehlender Deadline, unbounded Retention, Cross-Chain-Datenübertritt,
unklarem Generation-Stand oder Kostenüberschreitung. Danach vorherigen
Zustand unverändert lassen und den Gate-Status als HOLD dokumentieren.

## Abschluss

Nach dem Live-Lauf werden STP, Development Diary, Manual, Proposal Dossier und
Open-Item-Bericht aktualisiert. Ein daraus entstehender Änderungs-PR bleibt
Draft, bis Review und alle Gates belegt sind; Aktivierung erfolgt nicht
automatisch.

Bis zu dieser Freigabe ist der aktuelle Fork-Stand offline abgeschlossen und
die vier Live-Gates bleiben unverändert `HOLD / NOT RUN`.
