# OVSA Console

React + TypeScript frontend for [RFC OVSA-CONSOLE-001](https://github.com/volcengine/OpenViking/discussions/7700), with an isolated in-memory Mock Server for reviewing Slices A–D. The production Shared Web integration remains read-only; demo mutations never pass through it.

## Frontend

- Instance health, Connector degradation, Runtime snapshot, Worker slots, tasks, delivery stages, Sessions, Activity, and metrics.
- Capability `enabled / available / effective` states and actionable reason codes.
- Task cancellation with `expectedRevision`, confirmation of external side effects, and separate `stopRequested` / `termination` states.
- Runtime, model, and effort transitions; new cognition Session confirmation.
- Allowlisted configuration editing, validate-only probes, `If-Match`, desired/effective drift, instance process controls, setup, and persistent Operation steps.
- Provider health and bindings, validate-only checks, write-only credential rotation, logs, diagnostics, and redacted audit entries.
- Responsive navigation and light/dark themes.

Outside explicit demo mode the UI issues GET requests only. The Shared Web allowlist still proxies only Slice A GET routes. This PR does **not** implement production owner authentication, Local Control Broker, PM2 actions, TaskManager writes, Provider secret storage, or real diagnostics.

## Run the interactive demo

From `superagent/`:

```sh
npm install --include=dev
npm run demo --workspace @ov-superagent/console
```

Open **http://127.0.0.1:3740/?demo=1**. The command builds the frontend and starts a loopback-only server serving the bundle plus the RFC API. State is in memory and resets when the process stops. The page always displays an “Interactive mock demo” banner.

For hot-reload frontend work, use two terminals:

```sh
node packages/console/test/mock-server.mjs
npm run demo:dev --workspace @ov-superagent/console
```

Open **http://127.0.0.1:5173/console/?demo=1**. `demo:dev` points Vite at the mock rather than Shared Web on 3737. Without `?demo=1`, controls stay disabled even if a backend reports an effective control module.

The Mock Server exercises the contract rather than a happy-path-only UI:

- same-origin and CSRF checks, viewer boundaries, body limits, and structured RFC errors;
- unknown/repeated query rejection, cursor parameter checks, and log stream allowlists;
- `expectedRevision` / `If-Match` conflicts and idempotency replay/conflict handling;
- 202 Operations with independent validate/save/restart/verify steps;
- no secret persistence or return values beyond an opaque `credentialRef`.

It is test/demo code under `packages/console/test/`; no production module imports it.

## Legacy backend behavior

When v1 discovery returns 404, the console uses the existing GET endpoints for health, Connector, Session, and Activity observation. Task and metric panels show `workers_not_merged`; unverifiable Runtime details stay unavailable. No Task data is inferred from message activities. The previous timeline remains at `/activity`.

## Build and test

```sh
npm run build:console
npm run test:console
npm run test:browser --workspace @ov-superagent/console
npm run test:demo-browser --workspace @ov-superagent/console
```

The normal browser suite retains its assertion that production Shared Web flows issue GET only. The demo suite drives Runtime effort, configuration apply, credential rotation, Task cancellation, and mobile layout against the Mock Server. Screenshots are written to `docs/console/` (`demo-*.png`).

The build is committed under `packages/web/public/console/` because the release packager archives committed files rather than running a frontend build. Rebuild after changing console source.

Install Chromium once if needed:

```sh
npx playwright install chromium
```
