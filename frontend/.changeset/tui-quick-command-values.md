---
'@openviking/react': minor
---

Change Tui and Console quickCommands from command-name strings to { name, value } entries. Keep names and descriptions in the command catalogue, and fill the supplied complete value verbatim on selection without automatically executing it. Migrate each existing string to an object; Console defaults retain their ov command prefixes. Empty-state documentation presets follow the Volcengine examples, including the saved-configuration compilation workflow.

Recognize HTTP and HTTPS URLs as native links in Tui input, command echoes and results so they use the host theme primary color, just like Viking resource URIs.
