// Magpie's moejs module: no Node/Bun imports. Only the selection belongs here;
// the Bun bridge validates and applies it through OpenViking's versioned API.
export const agent = {
  id: "openviking-models",
  name: "OpenViking / VikingBot",
  bin: "openviking-server",
  dir: "~/.openviking",
  config: "~/.config/magpie-openviking-models/selection.json",
  format: "json",
  model: "model",
  ua: ["openviking", "vikingbot"],
  notice: "Check the adapter status, then restart OpenViking and VikingBot to apply saved settings. Disconnect before uninstalling. 请检查适配器状态，保存后重启 OV 和 VikingBot；卸载前先断开。",
};

export function connect({ gateway, model }) {
  return {
    "gateway.baseURL": gateway.v1,
    "gateway.key": gateway.key,
    "metadata.context": model.context || null,
    "metadata.output": model.output || null,
    "metadata.images": typeof model.images === "boolean" ? model.images : null,
  };
}
