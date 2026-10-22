/**
 * DSH browser half of the OpenViking memory bundle.
 *
 * The bundle's Host half owns the session's memory lifecycle and mounts the
 * console routes; this half adds the one thing the Host cannot draw: an
 * OpenViking entry in the left sidebar's global panel list, which opens the
 * server's Studio in the center column, inside DSH. The browser half and the
 * Host rows belong to this one bundle, so DSH enables and unloads them together.
 *
 * Host facts arrive through one fenced same-origin route
 * (console.mjs -> /openviking/console); the iframe is served by the bundle's
 * same-origin /studio/* proxy, which makes the Studio document reachable for
 * theme injection. The proxy never carries the API key.
 *
 * The file is intentionally a single plain script: DSH loads browser halves
 * through window.__ModuleLoader__ and supplies React through `require`.
 */
window.__ModuleLoader__.load({
  id: "@openviking/dsh-memory-plugin",
  factory(require) {
    "use strict";

    const React = require("react");
    const h = React.createElement;

    const LOCALE_NS = "openvikingConsole";
    const CONSOLE_ROUTE = "/openviking/console";
    // The sidebar entry id addresses the matching `main` panel key.
    const PANEL_ID = "openviking-console";
    const ROUTE_TIMEOUT_MS = 6000;

    const ZH = {
      panelName: "OpenViking 控制台",
      title: "OpenViking 控制台",
      refresh: "刷新",
      openInBrowser: "在浏览器中打开",
      loading: "正在加载 OpenViking 控制台…",
      unreachableTitle: "无法连接 OpenViking 服务",
      unreachableHint: "请确认 OpenViking 服务已启动，并检查插件配置的 endpoint。",
      missingTitle: "此服务未提供 Web 控制台",
      missingHint: "当前 OpenViking 服务没有内置 Studio（或 /studio 不可访问）。请升级服务，或用 consoleUrl 指向可访问的控制台地址。",
      errorTitle: "无法读取控制台配置",
      errorHint: "DSH 的 OpenViking 插件路由没有响应。请刷新页面或重启 DSH 后重试。",
      retry: "重试",
    };

    const EN = {
      panelName: "OpenViking Console",
      title: "OpenViking Console",
      refresh: "Refresh",
      openInBrowser: "Open in browser",
      loading: "Loading the OpenViking console…",
      unreachableTitle: "Cannot reach the OpenViking service",
      unreachableHint: "Make sure the OpenViking server is running and the plugin's endpoint is correct.",
      missingTitle: "This server has no Web console",
      missingHint: "The OpenViking server does not ship the Studio (or /studio is unreachable). Upgrade it, or point consoleUrl at a reachable console.",
      errorTitle: "Cannot read the console settings",
      errorHint: "The DSH route did not answer. Refresh the page or restart DSH and try again.",
      retry: "Retry",
    };

    let translate = key => key;

    // ── Host theme ─────────────────────────────────────────────────────────
    // The plugin proxies the Studio through the DSH origin, so the iframe is
    // same-origin and this code can set the Studio's CSS variables directly.
    // A `color-scheme` style on the iframe also flips the Studio's own
    // `prefers-color-scheme` for components that read it.
    const DSH_TOKENS = [
      "--dsw-alias-bg-base",
      "--dsw-alias-bg-layer-1",
      "--dsw-alias-bg-layer-2",
      "--dsw-alias-bg-overlay",
      "--dsw-alias-border-l1",
      "--dsw-alias-border-l2",
      "--dsw-alias-brand-primary",
      "--dsw-alias-label-primary",
      "--dsw-alias-label-secondary",
      "--dsw-alias-state-error-primary",
      "--dsw-alias-state-success-primary",
      "--dsw-alias-state-warn-primary",
      "--dsw-alias-state-idle-primary",
      "--dsw-specific-sidebar-fill",
    ];

    let themeState = { scheme: "light", tokens: {}, revision: 0 };
    const themeListeners = new Set();

    function subscribeTheme(listener) {
      themeListeners.add(listener);
      return () => themeListeners.delete(listener);
    }

    function setThemeState(snapshot) {
      // The revision increments on every theme/change, even when the light/dark
      // scheme is unchanged, so a same-mode palette switch still reaches the
      // Studio's CSS variables.
      themeState = {
        scheme: snapshot?.active?.colorScheme === "dark" ? "dark" : "light",
        tokens: snapshot?.active?.tokens || {},
        revision: themeState.revision + 1,
      };
      for (const listener of themeListeners) listener();
    }

    function useThemeState() {
      return React.useSyncExternalStore(subscribeTheme, () => themeState);
    }

    function readDshTokens(scope) {
      if (typeof document === "undefined") return {};
      // Custom properties inherit downward. A skin or theme layer may define
      // them on a wrapper below <html>, so resolve from the plugin's own page
      // (inside the themed tree) first, then fall back to body and the root.
      const elements = [scope, document.body, document.documentElement].filter(Boolean);
      const tokens = {};
      for (const element of elements) {
        const style = getComputedStyle(element);
        for (const name of DSH_TOKENS) {
          if (tokens[name]) continue;
          const value = style.getPropertyValue(name).trim();
          if (value) tokens[name] = value;
        }
      }
      // A theme layer whose tokens this document cannot resolve still exists in
      // the theme snapshot; accept the spellings a snapshot may use for each
      // alias (full `--dsw-alias-*`, bare alias, or no leading `--`).
      const snapshotTokens = themeState.tokens || {};
      const candidates = name => [
        name,
        name.replace(/^--/, ""),
        name.replace(/^--dsw-alias-/, ""),
        name.replace(/^--dsw-specific-/, ""),
      ];
      for (const name of DSH_TOKENS) {
        if (tokens[name]) continue;
        for (const key of candidates(name)) {
          const value = snapshotTokens[key];
          if (typeof value === "string" && value.trim()) {
            tokens[name] = value.trim();
            break;
          }
        }
      }
      return tokens;
    }

    /** Map the host palette onto the Studio's shadcn-style variables. */
    function buildThemeOverrides(tokens) {
      const base = tokens["--dsw-alias-bg-base"];
      if (!base) return {};
      const layer1 = tokens["--dsw-alias-bg-layer-1"] || base;
      const layer2 = tokens["--dsw-alias-bg-layer-2"] || layer1;
      const overlay = tokens["--dsw-alias-bg-overlay"] || layer1;
      const border1 = tokens["--dsw-alias-border-l1"] || layer2;
      const border2 = tokens["--dsw-alias-border-l2"] || border1;
      const label1 = tokens["--dsw-alias-label-primary"] || base;
      const label2 = tokens["--dsw-alias-label-secondary"] || label1;
      const brand = tokens["--dsw-alias-brand-primary"] || label1;
      const error = tokens["--dsw-alias-state-error-primary"] || label1;
      const success = tokens["--dsw-alias-state-success-primary"] || brand;
      const warning = tokens["--dsw-alias-state-warn-primary"] || brand;
      const sidebar = tokens["--dsw-specific-sidebar-fill"] || layer1;
      const mix = (color, percent, other) =>
        `color-mix(in oklab, ${color} ${percent}%, ${other})`;
      return {
        "--background": base,
        "--foreground": label1,
        "--card": layer1,
        "--card-foreground": label1,
        "--popover": overlay,
        "--popover-foreground": label1,
        "--primary": brand,
        "--primary-foreground": base,
        "--secondary": layer2,
        "--secondary-foreground": label1,
        "--muted": layer2,
        "--muted-foreground": label2,
        "--accent": mix(brand, 10, base),
        "--accent-foreground": brand,
        "--destructive": error,
        "--border": border1,
        "--input": border2,
        "--ring": brand,
        "--sidebar": sidebar,
        "--sidebar-foreground": label1,
        "--sidebar-primary": brand,
        "--sidebar-primary-foreground": base,
        "--sidebar-accent": mix(brand, 14, base),
        "--sidebar-accent-foreground": brand,
        "--sidebar-border": border1,
        "--sidebar-ring": brand,
        "--chart-1": mix(brand, 55, base),
        "--chart-2": mix(brand, 70, base),
        "--chart-3": brand,
        "--chart-4": mix(brand, 72, label1),
        "--chart-5": mix(brand, 52, label1),
        "--home-token-input": brand,
        "--home-token-output": mix(brand, 60, success),
        "--home-token-embedding": mix(brand, 55, label2),
        "--heatmap-empty": layer2,
        "--heatmap-level-1": mix(brand, 25, base),
        "--heatmap-level-2": mix(brand, 45, base),
        "--heatmap-level-3": mix(brand, 65, base),
        "--heatmap-level-4": brand,
        "--success": success,
        "--success-foreground": base,
        "--warning": warning,
        "--warning-foreground": base,
        "--info": brand,
        "--info-foreground": base,
      };
    }

    /** Apply the host palette to the proxied Studio document. */
    function applyFrameTheme(iframe, theme) {
      if (!iframe) return false;
      let doc;
      try {
        doc = iframe.contentDocument;
      } catch {
        return false;
      }
      if (!doc) return false;
      const root = doc.documentElement;
      if (!root) return false;
      const tokens = readDshTokens(iframe.parentElement);
      if (!tokens["--dsw-alias-bg-base"]) return false;
      if (typeof console !== "undefined") {
        console.debug("[openviking] console theme", {
          sameOrigin: Boolean(doc),
          tokenCount: Object.keys(tokens).length,
          base: tokens["--dsw-alias-bg-base"],
          scheme: theme.scheme,
        });
      }
      root.classList.toggle("dark", theme.scheme === "dark");
      root.style.colorScheme = theme.scheme;
      for (const [name, value] of Object.entries(buildThemeOverrides(tokens))) {
        root.style.setProperty(name, value);
      }
      root.dataset.dshTheme = theme.scheme;
      return true;
    }

    // ── Sidebar icon ───────────────────────────────────────────────────────
    // The sidebar owns the row and its button; a panel-list entry contributes
    // the icon (owner props: size, active) and the localized label metadata.
    function PanelIcon(props) {
      const size = typeof props?.size === "number" ? props.size : 18;
      const active = Boolean(props?.active);
      return h(
        "svg",
        {
          viewBox: "0 0 24 24",
          width: size,
          height: size,
          fill: "currentColor",
          "aria-hidden": "true",
          style: {
            display: "block",
            color: active ? "var(--dsw-alias-brand-primary)" : "var(--dsw-alias-label-secondary)",
          },
        },
        h("path", { transform: "scale(.24)", d: "M20.5 84H31L70 16.45H67ZM37 84 76 16.45V84Z" }),
      );
    }

    // ── Center-column console page ─────────────────────────────────────────
    function ConsolePage() {
      const [state, setState] = React.useState({
        phase: "loading",
        info: null,
        error: null,
        frameLoaded: false,
      });
      const [nonce, setNonce] = React.useState(0);
      const theme = useThemeState();

      React.useEffect(() => {
        let active = true;
        setState(prev => ({ ...prev, phase: "loading", error: null, frameLoaded: false }));
        (async () => {
          try {
            const response = await fetch(new URL(CONSOLE_ROUTE, document.baseURI), {
              headers: { accept: "application/json" },
              cache: "no-store",
              signal: AbortSignal.timeout(ROUTE_TIMEOUT_MS),
            });
            const body = await response.json().catch(() => null);
            if (!active) return;
            if (!response.ok || !body || body.ok !== true) {
              throw new Error(body?.error?.message || `HTTP ${response.status}`);
            }
            setState({ phase: "loaded", info: body, error: null, frameLoaded: false });
          } catch (error) {
            if (!active) return;
            setState(prev => ({ ...prev, phase: "error", error: messageOf(error) }));
          }
        })();
        return () => {
          active = false;
        };
      }, [nonce]);

      const refresh = () => setNonce(value => value + 1);
      const info = state.info;
      const view = state.phase === "error" ? "error" : state.phase === "loading" ? "loading" : viewOf(info);
      const browserUrl = info?.browserUrl;

      return h(
        "div",
        { className: "ov-console-page", style: { colorScheme: theme.scheme } },
        h(Styles),
        h("div", { className: "ov-console-body" }, renderBody(state, view, refresh, nonce, theme, t)),
        // The console fills the pane; these two controls float over it instead
        // of occupying a separate toolbar.
        h(
          "div",
          { className: "ov-console-float", role: "toolbar", "aria-label": t("title") },
          h(
            "button",
            {
              type: "button",
              className: "ov-console-iconbtn",
              title: t("refresh"),
              "aria-label": t("refresh"),
              onClick: refresh,
            },
            RefreshIcon(),
          ),
          h(
            "button",
            {
              type: "button",
              className: "ov-console-iconbtn",
              title: t("openInBrowser"),
              "aria-label": t("openInBrowser"),
              onClick: () => openInBrowser(browserUrl),
              disabled: !browserUrl,
            },
            ExternalIcon(),
          ),
        ),
      );
    }

    function renderBody(state, view, refresh, nonce, theme, t) {
      const info = state.info;
      if (view === "loading") return h(Loading, { label: t("loading") });
      if (view === "ready") {
        return h(ConsoleFrame, {
          src: studioFrameUrl(),
          title: t("title"),
          label: t("loading"),
          reloadKey: nonce,
          theme,
        });
      }
      const spec = STATE_SPECS[view] || STATE_SPECS.error;
      const url = info?.browserUrl;
      const actions = [
        spec.retry
          ? h("button", { key: "retry", type: "button", className: "ov-console-button", onClick: refresh }, t("retry"))
          : null,
        url
          ? h("button", {
            key: "browser",
            type: "button",
            className: "ov-console-button ov-console-button--primary",
            onClick: () => openInBrowser(url),
          }, t("openInBrowser"))
          : null,
      ].filter(Boolean);
      return h(
        "div",
        { className: "ov-console-state" },
        h("div", { className: "ov-console-state__icon", "aria-hidden": "true" }, AlertIcon()),
        h("h2", null, t(spec.title)),
        h("p", null, t(spec.hint)),
        url ? h("code", null, url) : null,
        actions.length ? h("div", { className: "ov-console-state__actions" }, actions) : null,
      );
    }

    function studioFrameUrl() {
      try {
        return new URL("/studio/home", window.location.origin).href;
      } catch {
        return "/studio/home";
      }
    }

    const STATE_SPECS = {
      error: { title: "errorTitle", hint: "errorHint", retry: true },
      unreachable: { title: "unreachableTitle", hint: "unreachableHint", retry: true },
      missing: { title: "missingTitle", hint: "missingHint", retry: true },
    };

    function viewOf(info) {
      if (!info) return "error";
      const studio = info.studio || {};
      const health = info.health || {};
      if (studio.available) return "ready";
      if (health.reachable) return "missing";
      return "unreachable";
    }

    function Loading({ label }) {
      return h(
        "div",
        { className: "ov-console-loading" },
        h("span", { className: "ov-console-spinner", "aria-hidden": "true" }),
        h("span", null, label),
      );
    }

    // The loading veil belongs to the frame, not to the page: a fresh reload
    // remounts this component, which resets the veil without touching the
    // page's own state or the React-managed DOM by hand.
    function ConsoleFrame({ src, title, label, reloadKey, theme }) {
      const [loaded, setLoaded] = React.useState(false);
      const iframeRef = React.useRef(null);
      const observerRef = React.useRef(null);
      const observedRef = React.useRef(null);
      const schemeRef = React.useRef(theme.scheme);
      schemeRef.current = theme.scheme;

      const applyTheme = React.useCallback(() => {
        applyFrameTheme(iframeRef.current, { scheme: schemeRef.current });
      }, []);

      const ensureObserver = React.useCallback(() => {
        const iframe = iframeRef.current;
        if (!iframe) return;
        let root;
        try {
          root = iframe.contentDocument?.documentElement;
        } catch {
          return;
        }
        // The initial about:blank document is replaced by the proxied Studio;
        // only observe the document that is actually loaded.
        if (!root || observedRef.current === root) return;
        observerRef.current?.disconnect();
        observerRef.current = new MutationObserver(() => {
          const doc = iframeRef.current?.contentDocument;
          if (!doc) return;
          const wantDark = schemeRef.current === "dark";
          if (doc.documentElement.classList.contains("dark") !== wantDark) {
            applyTheme();
          }
        });
        observerRef.current.observe(root, { attributes: true, attributeFilter: ["class"] });
        observedRef.current = root;
      }, [applyTheme]);

      React.useEffect(() => {
        applyTheme();
      }, [applyTheme, theme.scheme, theme.revision]);

      React.useEffect(() => {
        ensureObserver();
        return () => {
          observerRef.current?.disconnect();
          observerRef.current = null;
          observedRef.current = null;
        };
      }, [ensureObserver, src, reloadKey]);

      return h(
        React.Fragment,
        null,
        h("iframe", {
          key: `${src}#${reloadKey}`,
          ref: iframeRef,
          className: "ov-console-frame",
          src,
          title,
          allow: "clipboard-write",
          referrerPolicy: "same-origin",
          // The embedded Studio's `prefers-color-scheme` follows this element's
          // computed color scheme, so its own "system" parts track DSH.
          style: { colorScheme: theme.scheme },
          onLoad: () => {
            setLoaded(true);
            applyTheme();
            ensureObserver();
          },
        }),
        loaded ? null : h(Loading, { label }),
      );
    }

    function openInBrowser(url) {
      if (!url) return;
      window.open(url, "_blank", "noopener,noreferrer");
    }

    function messageOf(error) {
      return error instanceof Error ? error.message : String(error);
    }

    // ── Artwork ────────────────────────────────────────────────────────────
    function RefreshIcon() {
      return h(
        "svg",
        {
          viewBox: "0 0 24 24",
          width: 16,
          height: 16,
          fill: "none",
          stroke: "currentColor",
          strokeWidth: 1.8,
          strokeLinecap: "round",
          strokeLinejoin: "round",
          "aria-hidden": "true",
        },
        h("path", { d: "M20 12a8 8 0 1 1-2.34-5.66" }),
        h("path", { d: "M20 4v5h-5" }),
      );
    }

    function ExternalIcon() {
      return h(
        "svg",
        {
          viewBox: "0 0 24 24",
          width: 16,
          height: 16,
          fill: "none",
          stroke: "currentColor",
          strokeWidth: 1.8,
          strokeLinecap: "round",
          strokeLinejoin: "round",
          "aria-hidden": "true",
        },
        h("path", { d: "M14 4h6v6M20 4l-9 9" }),
        h("path", { d: "M18 13v6H5V6h6" }),
      );
    }

    function AlertIcon() {
      return h(
        "svg",
        {
          viewBox: "0 0 24 24",
          width: 30,
          height: 30,
          fill: "none",
          stroke: "currentColor",
          strokeWidth: 1.6,
          strokeLinecap: "round",
          "aria-hidden": "true",
        },
        h("circle", { cx: 12, cy: 12, r: 9 }),
        h("path", { d: "M12 7.5v5.5M12 16.5v.01" }),
      );
    }

    // ── Styles ─────────────────────────────────────────────────────────────
    function Styles() {
      return h("style", null, CSS);
    }

    const CSS = `
.ov-console-page {
  position: relative;
  display: flex; flex-direction: column; width: 100%; height: 100%; min-height: 0;
  background: var(--dsw-alias-bg-base); color: var(--dsw-alias-label-primary);
}
.ov-console-float {
  position: absolute; right: 14px; bottom: 14px; z-index: 2;
  display: flex; align-items: center; gap: 2px; padding: 3px;
  border: 1px solid var(--dsw-alias-border-l1); border-radius: 999px;
  background: var(--dsw-alias-bg-overlay);
  box-shadow: 0 2px 12px rgba(0, 0, 0, .18);
}
.ov-console-float .ov-console-iconbtn { width: 26px; height: 26px; border-radius: 999px; }
.ov-console-float .ov-console-iconbtn:disabled { opacity: .4; cursor: default; }
.ov-console-float .ov-console-iconbtn:disabled:hover { background: transparent; color: var(--dsw-alias-label-secondary); }
.ov-console-iconbtn {
  display: inline-flex; align-items: center; justify-content: center;
  width: 28px; height: 28px; padding: 0; border: 0; border-radius: 6px;
  background: transparent; color: var(--dsw-alias-label-secondary); cursor: pointer;
}
.ov-console-iconbtn:hover { background: var(--dsw-alias-bg-layer-2); color: var(--dsw-alias-label-primary); }
.ov-console-iconbtn:focus-visible { outline: 2px solid var(--dsw-alias-brand-primary); outline-offset: 1px; }
.ov-console-body { position: relative; flex: 1; min-height: 0; background: var(--dsw-alias-bg-base); }
.ov-console-frame { display: block; width: 100%; height: 100%; border: 0; background: var(--dsw-alias-bg-base); }
.ov-console-loading {
  position: absolute; inset: 0; display: flex; align-items: center; justify-content: center; gap: 10px;
  background: var(--dsw-alias-bg-base); color: var(--dsw-alias-label-secondary); font-size: 13px;
}
.ov-console-spinner {
  width: 18px; height: 18px; border: 2px solid var(--dsw-alias-border-l2);
  border-top-color: var(--dsw-alias-brand-primary); border-radius: 50%;
  animation: ov-console-spin .8s linear infinite;
}
@keyframes ov-console-spin { to { transform: rotate(360deg); } }
.ov-console-state {
  position: absolute; inset: 0; display: flex; flex-direction: column; align-items: center;
  justify-content: center; gap: 10px; padding: 32px; overflow: auto; text-align: center;
  color: var(--dsw-alias-label-secondary);
}
.ov-console-state__icon { display: flex; color: var(--dsw-alias-label-secondary); opacity: .8; }
.ov-console-state h2 { margin: 0; color: var(--dsw-alias-label-primary); font-size: 15px; font-weight: 600; }
.ov-console-state p { max-width: 560px; margin: 0; font-size: 13px; line-height: 1.6; }
.ov-console-state code {
  max-width: 560px; padding: 6px 10px; overflow-wrap: anywhere;
  border: 1px solid var(--dsw-alias-border-l1); border-radius: 6px;
  background: var(--dsw-alias-bg-layer-1); color: var(--dsw-alias-label-secondary);
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 12px;
}
.ov-console-state__actions { display: flex; gap: 8px; margin-top: 6px; }
.ov-console-button {
  height: 30px; padding: 0 14px; border: 1px solid var(--dsw-alias-border-l1); border-radius: 7px;
  background: var(--dsw-alias-bg-layer-1); color: var(--dsw-alias-label-primary);
  font-family: inherit; font-size: 12px; cursor: pointer;
}
.ov-console-button:hover { background: var(--dsw-alias-bg-layer-2); }
.ov-console-button--primary { border-color: var(--dsw-alias-brand-primary); color: var(--dsw-alias-brand-primary); }
@media (max-width: 720px) {
  .ov-console-float { right: 8px; bottom: 8px; }
}
`;

    // ── Locale and slots ───────────────────────────────────────────────────
    function apply(ctx) {
      ctx.effect(() => {
        const disposeZh = ctx.locale.register(LOCALE_NS, "zh", ZH);
        const disposeEn = ctx.locale.register(LOCALE_NS, "en", EN);
        translate = ctx.locale.bind(LOCALE_NS);
        return () => {
          translate = key => key;
          if (typeof disposeZh === "function") disposeZh();
          if (typeof disposeEn === "function") disposeEn();
        };
      }, "openvikingConsole.locale()");

      // Seed from the live snapshot, then follow every theme switch (including
      // the OS scheme flipping while DSH's preference is `system`, and a
      // same-mode palette switch that only changes token values).
      setThemeState(ctx.theme.getTheme());
      ctx.effect(() => {
        const dispose = ctx.on("theme/change", snapshot => setThemeState(snapshot));
        return () => {
          if (typeof dispose === "function") dispose();
        };
      }, "openvikingConsole.theme()");

      // The console UI and this companion bundle load and unload together, so
      // no availability probe is needed here: DSH owns the bundle lifecycle.
      // The sidebar entry id is the key the owner dispatches into `main`.
      ctx.slots.inject("main", () => ctx.slots.register({
        name: "main",
        key: PANEL_ID,
        locale: LOCALE_NS,
      }, ConsolePage));

      ctx.slots.inject("sidebar.panellist", () => ctx.slots.register({
        name: "sidebar.panellist",
        id: PANEL_ID,
        order: 30,
        label: () => t("panelName"),
        locale: LOCALE_NS,
      }, PanelIcon));
    }

    function t(key) {
      return translate(key);
    }

    return { inject: ["slots", "locale", "theme"], apply };
  },
});
