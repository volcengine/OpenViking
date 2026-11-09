const definitions = [
  ["max_tokens", "最大输出", "Max output tokens", 1, Number.MAX_SAFE_INTEGER, "1"],
  ["temperature", "温度", "Temperature", 0, 2, "any"],
  ["thinking", "思考模式", "Thinking"],
  ["timeout", "超时（秒）", "Timeout (seconds)", 0, null, "any"],
];
export function parameterFields(container, prefix, zh, values = {}) {
  container.replaceChildren();
  for (const [key, chinese, english, min, max, step] of definitions) {
    const row = document.createElement("div"); row.className = "parameter";
    const id = `${prefix}-${key}`;
    const label = document.createElement("label"); label.htmlFor = `${id}-mode`; label.textContent = zh ? chinese : english; label.title = key;
    const controls = document.createElement("div"); controls.className = "parameter-controls";
    const select = document.createElement("select"); select.id = `${id}-mode`; select.dataset.parameter = key;
    const choices = [["preserve", zh ? "沿用现有配置" : "Keep existing"]];
    if (key === "thinking") choices.push(["true", zh ? "开启" : "On"], ["false", zh ? "关闭" : "Off"]);
    else {
      if (key !== "timeout") choices.push(["null", zh ? "模型默认" : "Model default"]);
      choices.push(["custom", zh ? "自定义" : "Custom"]);
    }
    for (const [value, text] of choices) select.add(new Option(text, value));
    const value = values[key];
    select.value = !Object.hasOwn(values, key) ? "preserve" : value === null ? "null" : key === "thinking" ? String(value) : "custom";
    controls.append(select);
    if (key !== "thinking") {
      const input = document.createElement("input"); input.type = "number"; input.id = id; input.dataset.value = key;
      input.setAttribute("aria-label", `${zh ? chinese : english} (${key})`);
      input.min = String(min); if (max != null) input.max = String(max); input.step = step;
      const validate = () => input.setCustomValidity(key === "timeout" && input.value !== "" && input.valueAsNumber <= 0 ? (zh ? "超时必须大于 0 秒" : "Timeout must be greater than zero") : "");
      input.addEventListener("input", validate);
      input.value = typeof value === "number" ? String(value) : "";
      validate();
      const update = () => { input.disabled = select.value !== "custom"; input.hidden = input.disabled; input.required = !input.disabled; };
      select.addEventListener("change", update); update(); controls.append(input);
    }
    row.append(label, controls); container.append(row);
  }
}
export function readParameters(container, previous = {}) {
  const result = { ...previous };
  for (const select of container.querySelectorAll("[data-parameter]")) {
    const key = select.dataset.parameter;
    delete result[key];
    if (select.value === "preserve") continue;
    if (select.value === "null") result[key] = null;
    else if (select.value === "true" || select.value === "false") result[key] = select.value === "true";
    else {
      const input = container.querySelector(`[data-value="${key}"]`);
      const value = input.valueAsNumber;
      if (!input.checkValidity() || !Number.isFinite(value) || (key === "timeout" && value <= 0)) throw new Error("invalid_parameters");
      result[key] = value;
    }
  }
  return result;
}
