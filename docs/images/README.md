# `docs/images/`

Static assets plus the **Volcengine console** agent-setup copy.

- Public docs site lives in `docs/en/` and `docs/zh/`. That is a different tree.
- `docs/images/agents/{en,zh}/` is the short, cloud-only setup shown in the Volcengine OpenViking console. Keep it easier than the site: TOS installer, managed endpoint, API key from the console page.
- Hermes has no plugin. `hermes memory setup openviking` → keep **OpenViking Service (VolcEngine Cloud)** → paste the API key.

See `AGENTS.md.local` in this directory for the agent-facing rules.
