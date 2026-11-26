Copy and run the following commands step by step in your terminal to complete OpenViking CLI installation, configuration, and usage:

### Step 1: Install OpenViking CLI and enter the configuration flow:

```bash
npm i -g @openviking/cli && ov config
```

### Step 2: Configure the connection

The wizard sets the Base URL when you select a managed preset.

- Base URL: If the wizard asks for a URL, copy this address into your terminal:
```text
{{OPENVIKING_BASE_URL}}
```

- API Key: Copy the API Key shown on the page into your terminal

{{OPENVIKING_API_KEY_BLOCK}}

### Step 3: After configuration, run the following command to view CLI usage:

```bash
ov config validate
ov health
ov --help
```
