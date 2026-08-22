# Implementation Dossier — Lark/WebSockets Compatibility

## Reihenfolge

1. Baseline mit den installierten Versionen und vollständigen Warning-Messages
   reproduzieren.
2. RED-Tests für Lark-WebSockets-Import und Uvicorn-SansIO-Import ergänzen.
3. Abhängigkeiten in `pyproject.toml` anheben und `uv.lock` mit gezielter
   Paketaktualisierung regenerieren.
4. `websockets-sansio` in den OpenViking- und VikingBot-Serveraufrufen setzen.
5. Installationshinweise und lokales Merge-Gate aktualisieren.
6. Fokustests, Bot-Regression und den lokalen Voll-Lauf ausführen.
7. Dossiers/Evidence-Ledger aktualisieren und nur eigene Dateien committen.

## Rollback

Bei einem fehlgeschlagenen Voll-Gate werden ausschließlich die eigenen
Abhängigkeits-/Server-/Teständerungen zurückgenommen; der verifizierte Stand
`e0be2d46` bleibt der Wiederherstellungspunkt. Es erfolgt kein Service- oder
Rechner-Restart.

## Gate

PASS erfordert:

- fokussierte Kompatibilitätstests ohne WebSockets-Deprecation;
- Bootstrap-Test mit explizitem SansIO-Protokoll;
- `uv lock --check`;
- lokaler Runner unter `/Volumes/ExtremePro/projects/local-ci-gate` mit allen
  Checks PASS.
