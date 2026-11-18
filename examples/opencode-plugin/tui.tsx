import { Plugin } from "@opencode/plugin/tui"
import { createSignal } from "solid-js"
import { useTerminalDimensions } from "@opentui/solid"
import { startTuiControls } from "./lib/tui-controls.mjs"

export default Plugin.define({
  id: "openviking.tui",
  setup(context) {
    const [status, setStatus] = createSignal("OV: unchecked · /ov")
    return startTuiControls(context, {
      updateStatus: setStatus,
      renderStatus: () => <text fg={context.theme.text.muted}>{status()}</text>,
      renderDialog: (title: string, text: string) => {
        const dimensions = useTerminalDimensions()
        return (
          <box flexDirection="column" gap={1} paddingLeft={2} paddingRight={2} paddingBottom={1}>
            <text fg={context.theme.text.base}>{title}</text>
            <scrollbox maxHeight={Math.max(3, Math.min(20, dimensions().height - 12))} focused>
              <text fg={context.theme.text.base} wrapMode="word">{text}</text>
            </scrollbox>
            <text fg={context.theme.text.muted}>Scroll to read · esc to close</text>
          </box>
        )
      },
    })
  },
})
