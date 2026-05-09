# OpenViking Setup SOP (For Agent)

## Goal

Help the user install, configure, validate, and start OpenViking with the smallest viable path.

## General Principles

- Default to the normal end-user installation path; do not default to source builds
- Default to prebuilt packages; do not assume Go / Rust / C++ / CMake are required
- If configuration is uncertain, ask the user first; do not guess provider, model, api_base, api_key, or workspace
- Only move to the source-build path when installation clearly falls back to local compilation, or when the user explicitly asks for a source install

## SOP

### 1. Choose the path

First determine which category the user belongs to.

#### A. Standard minimal install
Use this path if any of the following is true:
- The user just wants OpenViking installed and running
- The user just wants to try or integrate OpenViking
- The user is not asking for source-level development
- The user is not asking to modify low-level native components

Execution path:
1. Install the Python package
2. Ask for model configuration
3. Generate `~/.openviking/ov.conf`
4. Run `openviking-server doctor`
5. Start `openviking-server`

#### B. Local-model install (Ollama)
Use this path if any of the following is true:
- The user explicitly wants local models
- The user explicitly wants Ollama
- The user does not want to fill in many model settings manually

Execution path:
1. Run `openviking-server init`
2. Run `openviking-server doctor`
3. Start `openviking-server`

#### C. Docker install
Use this path if any of the following is true:
- The user explicitly wants to install or run with Docker
- The user does not want to install the Python package directly on the host
- The user wants configuration and data persisted via a mounted volume

Execution path:
1. Confirm whether the user already has an `ov.conf`
2. If not, confirm model configuration first or guide them to run `openviking-server init` inside the container
3. Start the container with the image or `docker-compose.yml`
4. Verify `/health`

#### D. Windows install
Use this path if any of the following is true:
- The user is on Windows
- The user asks for Windows installation steps

Execution path:
1. Prefer the prebuilt-wheel path from the standard minimal install flow
2. Configure `OPENVIKING_CONFIG_FILE` with Windows shell syntax
3. Run `openviking-server doctor`
4. Start `openviking-server`
5. Only enter the Windows local-build path if wheels are unavailable or installation fails

#### E. Source build
Only use this path if one of the following is true:
- The user explicitly asks for a source install
- Installation fails and the error clearly indicates local compilation is required
- The current platform has no prebuilt wheel
- The user explicitly wants to modify or rebuild low-level native components

Only then explain the required toolchain:
- Go 1.22+
- Rust 1.91.1+
- A C++ compiler
- CMake

### 2. Ask questions

If the user has not provided a complete model configuration, ask first. Do not write the config file yet.

#### Required questions

1. Which model provider do you want to use?
   - `openai`
   - `azure`
   - `volcengine`
   - `openai-codex`
   - `ollama`

2. Have you already decided on:
   - the embedding model name
   - the VLM model name
   - the API key / auth method

3. Which directory should `storage.workspace` use?

#### Follow-up questions by provider

##### openai
- embedding model name
- VLM model name
- whether to use `https://api.openai.com/v1`
- whether the API key is ready

##### azure
- embedding deployment name
- VLM deployment name
- Azure API Base
- Azure API Key
- whether to use the default `api_version = 2025-01-01-preview`

##### volcengine
- embedding model name
- VLM model name
- whether to use `https://ark.cn-beijing.volces.com/api/v3`
- whether the API key is ready

##### openai-codex
- whether the user wants to complete Codex OAuth via `openviking-server init`
- VLM model name
- which provider and model to use for embedding

##### ollama
- whether the user is okay with running `openviking-server init` directly
- whether Ollama is already installed
- which local embedding / VLM models they want to use

#### Extra required questions for Docker

If the user chooses Docker, also confirm:
- whether they want `docker run` or `docker compose`
- whether the host already has `~/.openviking/ov.conf`
- whether they want to mount host `~/.openviking` to container `/app/.openviking`
- whether they want to inject the full JSON config through `OPENVIKING_CONF_CONTENT`

#### Extra required questions for Windows

If the user is on Windows, also confirm:
- whether they use PowerShell or cmd.exe
- whether they only want a prebuilt-wheel install
- if a local build becomes necessary, whether CMake and MinGW are already installed

### 3. Generate the config

Only write `~/.openviking/ov.conf` after the user has confirmed all required values.

#### Minimal config shape

```json
{
  "storage": {
    "workspace": "..."
  },
  "embedding": {
    "dense": {
      "provider": "...",
      "api_base": "...",
      "api_key": "...",
      "model": "..."
    }
  },
  "vlm": {
    "provider": "...",
    "api_base": "...",
    "api_key": "...",
    "model": "..."
  }
}
```

#### Optional fields

Only add these when the provider requires them, when the README examples explicitly include them, or when the user explicitly asks for them:
- `dimension`
- `api_version`
- `max_concurrent`
- `temperature`
- `max_retries`

#### Do not do these things

- Do not fill in fake API keys
- Do not fill in unconfirmed paths
- Do not copy README comments into the JSON file
- Do not guess model names or private API endpoints

### 4. Run commands

#### Path A: Standard minimal install

```bash
pip install openviking --upgrade --force-reinstall
```

After the user confirms the configuration, write `~/.openviking/ov.conf`, then run:

```bash
openviking-server doctor
openviking-server
```

#### Path B: Local-model install (Ollama)

```bash
openviking-server init
openviking-server doctor
openviking-server
```

#### Path C: Docker install

##### Option 1: Run the published image directly

If the user already has a local config directory, prefer:

```bash
docker run --rm \
  -p 1933:1933 \
  -p 8020:8020 \
  -v ~/.openviking:/app/.openviking \
  ghcr.io/volcengine/openviking:latest
```

Notes:
- The default config path inside the container is `/app/.openviking/ov.conf`
- Inside the container, `HOME=/app`
- Prefer mounting host `~/.openviking` to container `/app/.openviking` so config, CLI config, and workspace data persist

##### Option 2: Use `docker-compose.yml`

If the user prefers compose, the repo already includes an example with:
- image: `ghcr.io/volcengine/openviking:latest`
- ports: `1933:1933`, `8020:8020`
- volume: `~/.openviking:/app/.openviking`

In that case, have the user run this from the repo root:

```bash
docker compose up -d
```

##### Option 3: Initialize config inside the container

If the user does not yet have `ov.conf`, there are two options:

1. Generate it on the host first and mount it into the container
2. Start the container, then run:

```bash
docker exec -it openviking openviking-server init
```

The Dockerfile also supports injecting the full JSON config via `OPENVIKING_CONF_CONTENT` on first start. Only use that if the user explicitly wants it and all config values have already been confirmed.

##### Docker validation

After startup, verify:

```bash
curl http://localhost:1933/health
```

#### Path D: Windows install

Prefer the prebuilt-wheel path first:

```bat
pip install openviking --upgrade --force-reinstall
```

After the config file is ready, set environment variables using the user’s shell.

##### PowerShell

```powershell
$env:OPENVIKING_CONFIG_FILE = "$HOME/.openviking/ov.conf"
```

##### cmd.exe

```bat
set "OPENVIKING_CONFIG_FILE=%USERPROFILE%\.openviking\ov.conf"
```

Then run:

```bat
openviking-server doctor
openviking-server
```

If the user also wants CLI config:

##### PowerShell

```powershell
$env:OPENVIKING_CLI_CONFIG_FILE = "$HOME/.openviking/ovcli.conf"
```

##### cmd.exe

```bat
set "OPENVIKING_CLI_CONFIG_FILE=%USERPROFILE%\.openviking\ovcli.conf"
```

#### Path E: Source build

Only after you have confirmed that the source-build path is necessary should you ask the user to prepare Go / Rust / C++ / CMake.

### 5. Triage

#### Case 1: Config file is missing, the path is wrong, or JSON cannot be parsed

Check first:
- whether `~/.openviking/ov.conf` exists
- whether an environment variable or `--config` points to the wrong path
- whether the config file is valid JSON

Handling rule:
- fix the config path or JSON syntax first
- then rerun `openviking-server doctor`

#### Case 2: Model configuration is incomplete

Typical signs:
- missing embedding or VLM config
- missing `provider` / `model` / `api_key`
- `openai-codex` is configured only for VLM, while embedding is still missing

Handling rule:
- fill in the minimal required config first
- do not guess model names or keys
- if the provider is `openai-codex`, remind the user that it mainly covers the VLM side and embedding still needs separate confirmation

#### Case 3: Model service is unreachable or auth is not usable

Check first:
- whether API Base is correct
- whether the API key / auth method is correct
- if using `openai-codex`, whether OAuth has been completed via `openviking-server init`
- if using Ollama, whether the service is actually running

Handling rule:
- fix provider config and auth state first
- for Ollama, prefer recommending:

```bash
openviking-server init
```

- then rerun:

```bash
openviking-server doctor
```

#### Case 4: Local dependency or packaged artifact is unavailable

Typical signs:
- the native engine module cannot be imported
- AGFS / RAGFS-related bindings are unavailable
- packaged artifacts are missing after installation

Handling rule:
- first try a standard reinstall:

```bash
pip install openviking --upgrade --force-reinstall
```

- if it still fails, then decide whether to move to the source-build path
- do not immediately require the full local build toolchain

#### Case 5: Installation falls into local compilation

Confirm first whether this is because:
- the current platform has no compatible wheel
- the user is already doing a source install
- prebuilt artifacts are unavailable

Handling rule:
- only after confirming the source-build path should you add Go / Rust / C++ / CMake
- on Windows, local compilation usually means CMake and MinGW become relevant first
- do not present source-build dependencies as the default install prerequisites

#### Case 6: Windows installation fails

Check in this order:
1. whether the current Python version / architecture matches a prebuilt wheel
2. whether installation actually fell into source compilation
3. whether environment variables were set correctly for PowerShell or cmd.exe
4. if local compilation is happening, whether CMake / MinGW are missing

Handling rule:
- fix wheel, path, and environment variable issues first
- only add build dependencies if local compilation is clearly required

#### Case 7: Docker starts but OpenViking is unusable

Check first:
- whether `~/.openviking` is correctly mounted to `/app/.openviking`
- whether `/app/.openviking/ov.conf` exists inside the container
- whether model configuration is complete
- whether `curl http://localhost:1933/health` succeeds
- whether the container still needs `openviking-server init`

Handling rule:
- fix volume mounts and config first
- then validate provider, model, and auth settings

#### Case 8: The user does not know which models to choose

Do not write the config yet.

Guidance rules:
- if the user already has an account with a specific cloud provider, prefer that provider first
- if the user wants local execution, prefer Ollama + `openviking-server init`
- if the user wants `openai-codex`, remind them that it mainly solves the VLM side and embedding still needs to be configured separately

## Additional reference
- [OpenViking GitHub repository](https://github.com/volcengine/OpenViking)
