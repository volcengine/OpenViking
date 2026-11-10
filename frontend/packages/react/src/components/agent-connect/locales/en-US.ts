const messages = {
  title: 'Connect Agent',
  mainstream: 'Connect mainstream agents',
  custom: 'Build your own agent',
  integration: 'Integration',
  instructions: 'Full instructions',
  copy: 'Copy and send to Agent',
  copyMarkdown: 'Copy as Markdown',
  copied: 'Copied',
  failed: 'Copy failed. Please retry.',
  notice:
    'Connected agents can read content available to you in OpenViking. Overseas services may transfer data abroad.',
  seeAgreement: ' See ',
  agreement: 'Terms of Service',
  apiKey: 'API Key',
  overseas: 'Overseas',
  hermesCustomIntro:
    '# Custom OpenViking server for Hermes\n\nConnect to the host-provided OpenViking server using the URL and API key in the setup steps below.\n\n',
  hermesCustomStep:
    '3. Choose **Custom** and enter {{OPENVIKING_BASE_URL}} as the server URL.',
  installerInstructions: (
    name: string,
    command: string,
  ) => `Install the OpenViking memory plugin for ${name} and help me start using it. OpenViking is a context database for AI agents (https://github.com/volcengine/OpenViking); the command and link below are from its official docs.

1. Run the official installer. It needs network access and writes to my home directory; if your sandbox blocks that, ask me for approval:

   ${command}

2. Once it's installed, read the OpenViking onboarding guide and use it to walk me through setup. The guide has you ask me before choices like importing history or adding a working rule. docs.openviking.ai and docs.openviking.net are the same docs site; use whichever loads: https://docs.openviking.ai/agents/en/onboarding.md

The API key is one I created for myself in the OpenViking console; the installer stores it in ~/.openviking/ovcli.conf on this machine. Use it in the command as it is; there's no need to show it in your replies, and don't write it into any other file or rule. Talk to me in English.`,
};
export default messages;
