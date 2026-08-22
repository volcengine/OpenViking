# Lessons Learned

## Codex-Compaction, Responses State und Legacy-VLM-H3

Stand: 2026-07-31
Status: Offline-Follow-up PASS; H1/H2, Live und Promotion bleiben HOLD

## 1. Evidenzgrenzen früh einfrieren

Die gezielte Legacy-Baseline `46 = 33 PASS + 13 FAIL` und die breite Baseline
`216 = 195 PASS + 21 FAIL` verhinderten, dass zwei stale Config-Assertions, elf
Streamingfehler und acht separate VolcEngine-Konstruktorfehler vermischt wurden.
Ein grüner Kandidatentest ersetzt keine rote Legacy-Suite, und eine reproduzierte
Baseline ist noch keine Reparatur.

## 2. No-Replay ist ein Cross-Layer-Vertrag

Ein Teilstream darf weder im Backend noch in Retry-Helpern, Failover-Wrappern
oder VikingBot erneut gesendet werden. Fortschritt muss vor der ersten
Eventauswertung feststehen. Catch-Phase-Snapshots sind präziser als pauschale
Vorher-/Nachher-Zustandsvergleiche, weil zulässige Pre-call-Selection getrennt
bleibt.

## 3. Feste Fehlerausgaben sind sicherer als variable Sanitizer

Bei markierten Fehlern sind drei exakte Konstanten für sichtbare Response,
Logger und Langfuse leichter mutationssensitiv zu prüfen als eine allgemeine
Sanitizer-Logik. H1 blieb dennoch offen, weil Definition allein weder Source noch
Senkentests ersetzt. Das korrekte Ergebnis ist verweigerter Source-Unlock.

## 4. Fail-closed benötigt erreichbare Work-Budgets

Graphlimits müssen nicht nur klein, sondern mit realistischen Cause-, Context-
und Aggregate-Kanten erreichbar und testbar sein. Die finale Definition nutzt
256 Nodes, 512 Edges und 256 Aggregate-Kinder. Getterfehler, malformed Strukturen
und Budgetüberlauf sind Sicherheitsfälle und liefern fail-closed.

## 5. Rekursive Wrapperprüfung gehört vor Selection

Ein Tool-Stream-Guard nur am aktiven oder flachen Ziel reicht nicht. Tiefe
heterogene und zyklische Zielgraphen müssen identitätssicher vor Failback,
Switcher-State, Provider und Vision-I/O validiert werden. Sichere Zyklen dürfen
terminieren und genau einen aktiven Provider erreichen.

## 6. Cancellation-Tests brauchen kontrollierte Barrieren

Sleeps sind kein Orakel. Getrennte Futures/Events, vorerzeugte
`CancelledError`-Objekte und ein einziger beobachteter Cleanup-Task machen
Priorität, Identität, genau-einmal Close und Orphan-Freiheit deterministisch.

## 7. MCP-Zugriff ist keine Provider-Capability

OpenViking MCP Health und ein echter read-only `search_experience`-Aufruf sind
wertvolle Betriebsbelege. Sie sagen nichts darüber aus, ob der Codex-Endpunkt
`context_management`, Compaction-Items oder Replay unter den geplanten Limits
unterstützt. Diese Evidenzarten müssen getrennt bleiben.

## 8. Ein maximales Revisionsbudget muss Konsequenzen haben

Die Security-Scores `78/100 → 84/100 → 89/100` verbesserten die Definition,
erreichten aber final nur `0C/1H/1M`. Nach der dritten zulässigen Revision wurde nicht
weitergemittelt: H2–H5 waren geschlossen, H1 blieb offen, Source-Unlock wurde
verweigert. Fail-closed HOLD ist ein valides Lieferergebnis.

## 9. Live bleibt eine eigene Nutzerentscheidung

Der User vertagte den Live-Provider-Test. Daher gibt es keinen Capability-Probe,
Canary oder Kostenbeleg. Ohne exakten HTTPS-Origin, einen Credential-Slot-
Fingerprint und feste Request-, Token-, Bildbyte- und Kostencaps darf aus
Offline- oder MCP-Evidenz keine Live-Freigabe abgeleitet werden.

## 10. Unveränderte Stop-Regeln

Für Legacy-VLM-H3 wurden Vertrags-Tests ergänzt und mit
`266 collected = 129 PASS + 137 fachliche RED` ausgeführt, aber keine
Produktionsdatei geändert. Es erfolgten kein Restart, keine Aktivierung, kein
Merge und keine Promotion. STP und Open Items werden separat vom zuständigen
Session-Transfer-Workflow gepflegt.

## 11. Ein neuer autorisierter Zyklus darf einen alten HOLD sauber ablösen

Der spätere User-Auftrag autorisierte einen neuen, unabhängigen Offline-Zyklus.
H1 sowie die neu gefundenen H6-/M2-Lücken wurden test-first geschlossen. Der
gezielte Satz bestand 272/272; Security Rev2 bestand mit 96/100, 0C/0H/1M.
Damit ist der Offline-HOLD aufgehoben, ohne das alte VETO umzudeuten. Der Live-
HOLD bleibt bestehen.

## 12. Fail-closed Markierung muss einen Ersatz zurückgeben dürfen

Nicht jede Exception erlaubt Instanzattribute. Eine Markierungsfunktion darf
deshalb nicht nur `setattr` ausführen: Sie braucht einen opaken,
klassenmarkierten Fallback mit Originalexception als `__cause__`, und jeder
Aufrufer muss das zurückgegebene Objekt behalten. Bei Aggregate-Grenzen reicht
ein vertrauensvolles `len()` nicht; die Schranke muss in jeder Iteration erneut
erzwungen werden.

## 13. Historische Baseline und aktuelles Inventar muessen getrennt bleiben

Die alte breite Evidenz `364 PASS + 8 FAIL` war korrekt fuer ihren Zeitpunkt,
aber die acht Faelle gehoerten zu einem verwaisten Testvertrag. Nach dessen
Ersatz durch drei aktuelle Chat-Completions-Tests ist sie kein aktueller
Restfehlerbestand. Ein restartbares Dossier muss deshalb historische
Ausgangsevidenz bewahren und zugleich ein eindeutig datiertes aktuelles
Testinventar benennen. Dasselbe gilt fuer Befundkennungen: `SEC-M2` verhindert,
dass der geschlossene Aggregate-Befund mit dem spaeteren Pydantic-M2 vermischt
wird.

Offline-Vollgruen bleibt trotzdem keine Live-Freigabe. Die finalen 102/102,
500/500 und 150/150 belegen den lokalen Scope; ohne freigegebene Modell-, Limit-,
Hash-, Preis- und Credential-Policies bleibt H1 vor I/O gesperrt und H2 darf
nicht beginnen. Ein wegen Headless-Berechtigung nicht verfuegbarer `agy`-Review
ist UNAVAILABLE und darf nicht zu PASS umgedeutet werden.
