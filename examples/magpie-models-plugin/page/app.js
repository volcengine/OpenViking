const field = (id) => document.getElementById(id);
await magpie.ready;
const options = await magpie.options.get();
field("manageVlm").checked = options.manageVlm ?? true;
field("restartOnChange").checked = options.restartOnChange ?? false;
field("botMode").value = options.botMode || "preserve";
for (const key of ["botModel", "serverUrl", "rootApiKeyFile"]) field(key).value = options[key] || "";
for (const key of ["vlmParameters", "botParameters"]) field(key).value = JSON.stringify(options[key] || {}, null, 2);
function update() {
  field("botModel").disabled = field("botMode").value !== "independent";
}
field("botMode").addEventListener("change", update);
update();
field("settings").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = event.currentTarget.querySelector("button");
  field("message").textContent = "";
  if (!field("manageVlm").checked && field("botMode").value === "follow") {
    field("message").textContent = "跟随 OV 时请同时开启 OV VLM 管理。 / Enable OV VLM management to follow it.";
    return;
  }
  button.disabled = true;
  try {
    // Merge with fresh options, preserving fields this form does not expose.
    const next = { ...await magpie.options.get(), manageVlm: field("manageVlm").checked, botMode: field("botMode").value, restartOnChange: field("restartOnChange").checked };
    for (const key of ["vlmParameters", "botParameters"]) {
      const value = JSON.parse(field(key).value || "{}");
      if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("invalid_parameters");
      next[key] = value;
    }
    for (const key of ["botModel", "serverUrl", "rootApiKeyFile"]) {
      const value = field(key).value.trim();
      if (value && (key !== "botModel" || next.botMode === "independent")) next[key] = value;
      else delete next[key];
    }
    await magpie.options.set(next);
    field("message").textContent = "设置已保存。请检查同步结果，再重启 OV 和 VikingBot。 / Settings saved; check synchronization before restarting.";
  } catch {
    field("message").textContent = "设置保存失败，请稍后重试。 / Could not save settings; try again.";
  } finally { button.disabled = false; }
});
