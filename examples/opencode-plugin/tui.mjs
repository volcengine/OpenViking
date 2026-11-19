import { Plugin } from "@opencode/plugin/tui"
import { useTerminalDimensions } from "@opentui/solid"
import { jsx } from "@opentui/solid/jsx-runtime"
import { startTuiControls } from "./lib/tui-controls.mjs"

export default Plugin.define({
  id: "openviking.tui",
  setup(context) {
    return startTuiControls(context, {
      renderDialog(title, text) {
        const dimensions = useTerminalDimensions()
        return jsx("box", {
          flexDirection: "column", gap: 1, paddingLeft: 2, paddingRight: 2, paddingBottom: 1,
          children: [
            jsx("text", { fg: context.theme.text.base, children: title }),
            jsx("scrollbox", {
              get maxHeight() { return Math.max(3, Math.min(20, dimensions().height - 12)) },
              focused: true,
              children: jsx("text", { fg: context.theme.text.base, wrapMode: "word", children: text }),
            }),
            jsx("text", { fg: context.theme.text.muted, children: "Scroll to read · esc to close" }),
          ],
        })
      },
    })
  },
})
