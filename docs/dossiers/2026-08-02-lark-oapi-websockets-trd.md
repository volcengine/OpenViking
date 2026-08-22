# Technical Requirement Dossier — Lark/WebSockets Compatibility

## Änderungsvertrag

| Datei | Änderung | Zweck |
|---|---|---|
| `pyproject.toml` | Lark/Uvicorn/WebSockets-Grenzen | Resolver darf keine inkompatible Kombination wählen |
| `uv.lock` | reproduzierbare Auflösung | Offline-Gate nutzt denselben Paketstand |
| `openviking/server/bootstrap.py` | `ws="websockets-sansio"` | kein impliziter Legacy-Adapter |
| `bot/vikingbot/cli/commands.py` | `ws="websockets-sansio"` | Bot-API verwendet denselben sicheren Pfad |
| Lark-Feishu-Fehlermeldungen | Installationsbereich `>=1.7.1,<2.0` | manuelle Reparatur darf nicht auf den alten SDK-Stand zurückfallen |
| `tests/test_lark_websockets_compat.py` | Import-Regressionsfälle und Warning-Ledger | WebSockets-Warnungen fail-loud erkennen; die zwei bekannten Upstream-Lark-Signaturen in einem frischen Subprozess sichtbar halten |
| `tests/server/test_bootstrap.py` | SansIO-Auswahl prüfen | Produktionsvertrag testen |
| `tests/parse/test_document_parser_threading.py` | lokaler `asyncio`-Proxy im Test-Helfer | keine process-globalen `to_thread`-Aufzeichnungen in der Root-Suite |
| `.local-ci-gate.toml` | Kompatibilitätscheck | lokales Gate blockiert Lock-/Runtime-Drift |

## Nicht-Ziele

- Kein Patchen von `.venv` oder installierten `site-packages`.
- Keine globale `warnings`-Unterdrückung.
- Keine Umstellung von Lark-HTTP-/Feishu-Verträgen.
- Kein Live-Feishu-/OpenClaw-/Provider-Aufruf.

## Rest-Risiko

`lark-oapi 1.7.1` vendort weiterhin eine Protobuf-Datei mit
`utcfromtimestamp` und einen Import-time-Aufruf von `asyncio.get_event_loop`.
Das ist ein upstream offener Befund. Der lokale Code dokumentiert und testet
die WebSockets-Grenze, behauptet aber nicht, diese zwei fremden Warnungen
behoben zu haben. Eine Behebung darf erst nach einem verifizierten Upstream-
Release mit Lock-Hash und Lifecycle-/Cleanup-Regressionen erfolgen.
