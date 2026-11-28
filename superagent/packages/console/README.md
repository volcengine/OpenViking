# OVSA Console

Read-only frontend for [RFC OVSA-CONSOLE-001](https://github.com/volcengine/OpenViking/discussions/7700), Slice A. It follows the Web Studio frontend stack: React, TypeScript, Vite, Lucide icons, and Vitest/Testing Library. The production bundle is served by the existing loopback-only Shared Web process.

## Included

- Instance health, Connector degradation, and Worker-facing state, with the fields the daemon cannot verify marked unavailable.
- Task list with status and runtime filters, title/ID search, and stable `before` cursor pagination. Polling only affects the latest page.
- Task details: persisted inputs, turn count, reports, and each report's Worker report → Router inbox → final IM delivery states, including an explicit `UNCERTAIN` warning.
- Cognition Sessions and redacted Activity from the existing feed, and all capability `enabled / available / effective` states with reason codes.
- Responsive navigation and light/dark themes.

There are no cancel, retry, pause, Runtime switch, configuration, Provider, process, or secret controls. The frontend does not call mutation APIs. The Shared Web allowlist likewise proxies only the RFC Slice A GET routes.

## The daemon's read-only routes

Tasks, inputs and reports are read from `/api/v1/` on the instance daemon, which answers with `SELECT`s over the tables the workers scheduler already keeps:

- `GET /api/v1/capabilities` — what this daemon can answer, and why not where it cannot.
- `GET /api/v1/instances/<id>/tasks?status&runtime&q&before&limit`
- `GET /api/v1/instances/<id>/tasks/<taskId>`
- `GET /api/v1/instances/<id>/tasks/<taskId>/inputs`, `/reports`

Token usage, live Runtime/model/effort snapshots, queue metrics and a task's linked Activity rows are not recorded by this daemon version, so those panels say unavailable with the reason instead of showing a zero. A task's report list and its delivery states are read from `task_reports`, `task_report_deliveries` and `outbox`.

## Older daemons

A daemon that predates these routes serves only `/api/activity`, `/health` and `/ready`. When v1 discovery returns 404, the console uses those GET endpoints for health, Connector, Session and Activity observation, and the task panel says the daemon predates the read-only task API. No task data is inferred from message activity.

## Signing in

When Shared Web has an API key configured, the console and the activity timeline share one key: both read and store it in `localStorage` under `superagent.web.apiKey`, so signing in on either page signs in on the other. `Lock` in the top bar removes it.

The previous message-level timeline remains at `/activity`. The new console is served at `/` and `/console`.

## Develop

From `superagent/`:

```sh
npm install --include=dev
npm run dev --workspace @ov-superagent/console
```

Vite listens on port 5173 and proxies API requests to Shared Web on 3737. Start Shared Web separately with its normal instance configuration.

## Build, test, and browser evidence

```sh
npm run build:console
npm run test:console
npm run test:browser --workspace @ov-superagent/console
```

The build is committed under `packages/web/public/console/` because the release packager archives committed files rather than running a frontend build. Rebuild after changing console source.

Browser tests start the real Shared Web server with an isolated RFC fixture daemon. Fixtures are test-only and are never mounted in production. The test checks desktop/mobile flows, legacy capability boundaries, browser errors, and that every request is GET. Screenshots are written to `docs/console/`.

Install Chromium once if needed:

```sh
npx playwright install chromium
```
