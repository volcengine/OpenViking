import { readParameters, parameterFields } from "./parameters.js";
const field = (id) => document.getElementById(id);
await magpie.ready;
const zh = magpie.theme?.lang?.startsWith("zh");
const words = zh ? {
  intro: "在 Agents → OpenViking 选择模型，在本插件页面设置用途、参数和重启方式。",
  manage: "由 Magpie 管理 VLM", vlmHint: "使用上方 OpenViking 选中的模型。",
  botMode: "VikingBot 模型管理方式", preserveBot: "保留 VikingBot 现有配置", follow: "跟随 OpenViking VLM", independent: "独立选择模型",
  botModel: "模型 ID", botHint: "仅管理已启用的 Bot，不会自动开启。独立模式留空时使用上方选中的模型。",
  connection: "连接设置", serverUrl: "服务地址", rootKey: "Root Key 文件路径（可选）",
  connectionHint: "默认读取 ovcli.conf 的地址和 root_api_key。文件路径位于运行 Magpie 的电脑上。远程服务使用 HTTPS。",
  restart: "改动后自动重启并验证", restartHint: "OV 需要支持重启 API；受管 VikingBot 随 OV 重启，独立 Bot 需另行重启。",
  save: "保存设置", saving: "保存中…", saved: "设置已保存，正在等待同步。", savedRestart: "设置已保存；同步成功后将自动重启并验证。",
  saveFailed: "保存失败，请重试。", loadFailed: "无法读取设置，请重新展开或刷新后重试。", followError: "跟随 VLM 时，请先开启 VLM 管理。",
  scope: "未指定的参数沿用现有配置。输出预算会按模型已知上限调整；Thinking 需模型支持。Embedding、Rerank 等保持现有设置。保存成功不代表服务已生效。",
} : {
  intro: "Pick a model in Agents → OpenViking; configure roles, parameters and activation on this plugin page.",
  manage: "Manage VLM with Magpie", vlmHint: "Uses the model selected for OpenViking above.",
  botMode: "VikingBot model management", preserveBot: "Keep VikingBot settings", follow: "Follow OpenViking VLM", independent: "Choose independently",
  botModel: "Model ID", botHint: "Only manages an enabled Bot; never enables it. Leave the independent model empty to use the Agent selection.",
  connection: "Connection settings", serverUrl: "Server URL", rootKey: "Root key file (optional)",
  connectionHint: "Reads the address and root_api_key from ovcli.conf by default. Paths belong to the computer running Magpie. Use HTTPS for remote servers.",
  restart: "Restart and verify after changes", restartHint: "Requires OV's restart API. A managed VikingBot restarts with OV; restart a standalone Bot separately.",
  save: "Save settings", saving: "Saving…", saved: "Settings saved; synchronization is pending.", savedRestart: "Settings saved; after synchronization, restart and verification will run automatically.",
  saveFailed: "Could not save settings. Try again.", loadFailed: "Could not load settings. Reopen this panel or refresh to retry.", followError: "Enable VLM management before following it.",
  scope: "Unspecified parameters keep existing settings. Output budgets follow known model limits; Thinking requires model support. Embedding and Rerank keep their settings. Saving does not confirm activation.",
};
for (const element of document.querySelectorAll("[data-i18n]")) element.textContent = words[element.dataset.i18n];
const button = field("saveSettings");
function update() {
  const independent = field("botMode").value === "independent";
  field("botModel").hidden = !independent;
  document.querySelector(".bot-model-label").hidden = !independent;
  field("botModel").disabled = !independent;
  field("vlmParameters").disabled = !field("manageVlm").checked;
  field("botParameters").disabled = field("botMode").value === "preserve";
}
field("botMode").addEventListener("change", update);
field("manageVlm").addEventListener("change", update);
try {
  const options = await magpie.options.get();
  field("manageVlm").checked = options.manageVlm ?? true;
  field("restartOnChange").checked = options.restartOnChange ?? false;
  field("botMode").value = options.botMode || "preserve";
  for (const key of ["botModel", "serverUrl", "rootApiKeyFile"]) field(key).value = options[key] || "";
  for (const key of ["vlmParameters", "botParameters"]) parameterFields(field(key), key, zh, options[key] || {});
  button.disabled = false;
  update();
} catch { field("message").textContent = words.loadFailed; }
// Sandboxed plugin panels cannot submit forms; save through the bridge.
field("settings").addEventListener("submit", (event) => event.preventDefault());
field("settings").addEventListener("keydown", (event) => {
  if (event.key === "Enter" && event.target.tagName === "INPUT") { event.preventDefault(); button.click(); }
});
button.addEventListener("click", async () => {
  if (button.disabled || !field("settings").reportValidity()) return;
  field("message").textContent = "";
  if (!field("manageVlm").checked && field("botMode").value === "follow") {
    field("message").textContent = words.followError;
    return;
  }
  // Freeze this plugin's own controls while its options bridge saves.
  const controls = [...field("settings").querySelectorAll("input, select, button, fieldset")];
  const disabled = controls.map((control) => control.disabled);
  for (const control of controls) control.disabled = true;
  button.textContent = words.saving;
  try {
    const next = { ...await magpie.options.get(), manageVlm: field("manageVlm").checked, botMode: field("botMode").value, restartOnChange: field("restartOnChange").checked };
    for (const key of ["vlmParameters", "botParameters"]) next[key] = readParameters(field(key), next[key]);
    for (const key of ["botModel", "serverUrl", "rootApiKeyFile"]) {
      const value = field(key).value.trim();
      if (value && (key !== "botModel" || next.botMode === "independent")) next[key] = value;
      else delete next[key];
    }
    await magpie.options.set(next);
    field("message").textContent = next.restartOnChange ? words.savedRestart : words.saved;
  } catch { field("message").textContent = words.saveFailed; }
  finally { controls.forEach((control, i) => { control.disabled = disabled[i]; }); button.textContent = words.save; }
});
