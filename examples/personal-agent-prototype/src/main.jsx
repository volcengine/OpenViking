import React, { useState, useEffect, useRef } from "react";
import { createRoot } from "react-dom/client";
import {
  BookOpen,
  Layers,
  Home,
  Brain,
  ShieldCheck,
  Plus,
  ArrowUpRight,
  ArrowLeft,
  ArrowRight,
  Search,
  X,
  Check,
  ChevronRight,
  FileText,
  Clock3,
  MoreHorizontal,
  Pause,
  Play,
  RotateCcw,
  RefreshCw,
  Link2,
  History,
  CircleAlert,
  CheckCircle2,
  Sparkles,
  Settings2,
  Trash2,
  Pencil,
  SlidersHorizontal,
  Menu,
  FlaskConical,
  Download,
  Loader2,
} from "lucide-react";
import {
  initialState,
  reduceState,
  STORAGE_KEY,
  suggestedPreference,
  effectivePreference,
} from "./model.js";
import "./styles.css";
function load() {
  try {
    let x = JSON.parse(localStorage.getItem(STORAGE_KEY));
    if (
      x?.schema === 1 &&
      Array.isArray(x.goals) &&
      x.goals.length &&
      Array.isArray(x.memories)
    )
      return x;
  } catch {}
  return initialState();
}
const navItems = [
  ["home", Home, "研究首页"],
  ["research", FileText, "研究文档"],
  ["memory", Brain, "知识与记忆"],
  ["settings", ShieldCheck, "授权设置"],
];
const badge = (text, type = "") => (
  <span className={`badge ${type}`}>{text}</span>
);
function Button({ children, icon: Icon, variant = "", ...props }) {
  return (
    <button className={`button ${variant}`} {...props}>
      {Icon && <Icon size={16} />} {children}
    </button>
  );
}
function Empty({ title, body, children }) {
  return (
    <div className="empty">
      <CheckCircle2 size={32} />
      <h3>{title}</h3>
      <p>{body}</p>
      {children}
    </div>
  );
}
function Overlay({ title, subtitle, children, onClose, compact = false }) {
  const titleId = React.useId();
  const ref = useRef(null);
  useEffect(() => {
    const prev = document.activeElement;
    ref.current?.showModal();
    return () => {
      prev?.focus?.();
    };
  }, []);
  return (
    <dialog
      ref={ref}
      aria-labelledby={titleId}
      className={compact ? "modal" : "drawer"}
      onCancel={(e) => {
        e.preventDefault();
        onClose();
      }}
      onClick={(e) => {
        if (e.target === ref.current) onClose();
      }}
    >
      <div className="dialog-inner">
        <header>
          <div>
            <h2 id={titleId}>{title}</h2>
            {subtitle && <p>{subtitle}</p>}
          </div>
          <button className="icon-button" aria-label="关闭" onClick={onClose}>
            <X size={20} />
          </button>
        </header>
        {children}
      </div>
    </dialog>
  );
}
function App() {
  const [state, setState] = useState(load),
    [page, setPage] = useState("home"),
    [goalId, setGoalId] = useState(() => load().goals[0].id),
    [tab, setTab] = useState("changes"),
    [activePatch, setActivePatch] = useState("scope"),
    [memoryId, setMemoryId] = useState("range"),
    [query, setQuery] = useState(""),
    [filter, setFilter] = useState("all"),
    [overlay, setOverlay] = useState(null),
    [toast, setToast] = useState(""),
    [menu, setMenu] = useState(false),
    [storageError, setStorageError] = useState(false),
    [simOpen, setSimOpen] = useState(false),
    [preview, setPreview] = useState(false);
  const goal = state.goals.find((g) => g.id === goalId) || state.goals[0];
  const memory =
    state.memories.find((m) => m.id === memoryId) || state.memories[0];
  const dispatch = (a) =>
    setState((s) => reduceState(s, { goalId: goal.id, ...a }));
  const announce = (s) => setToast(s);
  useEffect(() => {
    if (toast) {
      let t = setTimeout(() => setToast(""), 4000);
      return () => clearTimeout(t);
    }
  }, [toast]);
  useEffect(() => {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
      setStorageError(false);
    } catch {
      setStorageError(true);
    }
  }, [state]);
  useEffect(() => {
    let t = setInterval(
      () => setState((s) => reduceState(s, { type: "TICK", now: Date.now() })),
      500,
    );
    return () => clearInterval(t);
  }, []);
  const go = (p) => {
    setPage(p);
    setMenu(false);
    setQuery("");
    setPreview(false);
  };
  const openGoal = (id) => {
    setGoalId(id);
    go("research");
    setTab("changes");
    setActivePatch("scope");
  };
  const pending = goal.patches.filter((p) => p.status === "pending");
  const chosen = pending.filter((p) => p.selected);
  const patch =
    goal.patches.find((p) => p.id === activePatch) || goal.patches[0];
  const conflict = goal.baseVersion !== goal.version;
  const expired = goal.expiry < new Date().toISOString().slice(0, 10);
  const blocked =
    goal.running || goal.stale || conflict || !goal.grant || expired;
  const status = (g) =>
    g.running
      ? "研究中"
      : g.stale
        ? "待重算"
        : g.patches.some((p) => p.status === "pending")
          ? "待审阅"
          : g.paused
            ? "已暂停"
            : "已同步";
  const statusType = (g) =>
    g.running
      ? "purple"
      : g.stale
        ? "amber"
        : g.patches.some((p) => p.status === "pending")
          ? "purple"
          : "green";
  const toMemory = () => {
    setMemoryId("range");
    go("memory");
  };
  function download() {
    const blob = new Blob(
      [
        `# ${goal.docTitle}\n\n` +
          Object.entries(goal.document)
            .map(
              ([id, text]) =>
                `## ${{ scope: "执行与授权", memory: "长期记忆", range: "产品筛选" }[id] || id}\n${text}`,
            )
            .join("\n\n"),
      ],
      { type: "text/markdown;charset=utf-8" },
    );
    let url = URL.createObjectURL(blob);
    let a = document.createElement("a");
    a.href = url;
    a.download = `${goal.docTitle}.md`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  return (
    <div className="app-shell">
      <aside className={`sidebar ${menu ? "is-open" : ""}`}>
        <a
          className="brand"
          href="#"
          onClick={(e) => {
            e.preventDefault();
            go("home");
          }}
        >
          <span className="brand-mark">
            <Layers size={23} />
          </span>
          <strong>
            知续<span>个人研究助理</span>
          </strong>
        </a>
        <div className="workspace">
          <div className="avatar">L</div>
          <span>
            我的工作空间<small>个人空间</small>
          </span>
          <ChevronRight size={14} />
        </div>
        <nav aria-label="主导航">
          {navItems.map(([key, Icon, name]) => (
            <button
              key={key}
              className={page === key ? "active" : ""}
              onClick={() => go(key)}
            >
              <Icon size={19} />
              {name}
              {key === "research" &&
                state.goals.some((g) =>
                  g.patches.some((p) => p.status === "pending"),
                ) && <span className="nav-dot" />}
            </button>
          ))}
        </nav>
        <div className="sidebar-bottom">
          <div className="local-note">
            <span className="status-dot" /> 前端体验版
            <p>模拟数据，仅保存在此浏览器</p>
          </div>
          <button
            className="text-button"
            onClick={() => setOverlay({ type: "reset" })}
          >
            <RotateCcw size={15} /> 重置演示
          </button>
        </div>
      </aside>
      {menu && (
        <button
          className="nav-shade"
          aria-label="关闭导航"
          onClick={() => setMenu(false)}
        />
      )}
      <div className="main-shell">
        <header className="topbar">
          <div className="breadcrumb">
            <button
              className="icon-button mobile-only"
              aria-label="打开导航"
              onClick={() => setMenu(!menu)}
            >
              <Menu size={20} />
            </button>
            <span>我的空间</span>
            <ChevronRight size={14} />
            <strong>{navItems.find((x) => x[0] === page)[2]}</strong>
          </div>
          <div className="top-actions">
            <span className="demo-label">
              <FlaskConical size={14} /> 模拟体验
            </span>
            <button
              className="avatar small"
              aria-label="查看本地数据说明"
              onClick={() => setOverlay({ type: "about" })}
            >
              L
            </button>
          </div>
        </header>
        {storageError && (
          <div className="banner warning">
            浏览器存储不可用，刷新后可能丢失修改。可在文档页下载结果。
          </div>
        )}
        <main>
          {page === "home" && (
            <>
              <div className="page-heading">
                <div>
                  <h1>让研究，持续向前。</h1>
                  <p>把一个主题交给知续，把注意力留给值得判断的变化。</p>
                </div>
                <Button
                  icon={Plus}
                  variant="primary"
                  onClick={() => setOverlay({ type: "new" })}
                >
                  新建研究委托
                </Button>
              </div>
              <section className="attention">
                <div className="attention-icon">
                  <Sparkles size={22} />
                </div>
                <div>
                  <h3>
                    {state.goals.reduce(
                      (n, g) =>
                        n +
                        g.patches.filter((p) => p.status === "pending").length,
                      0,
                    )}{" "}
                    条建议，等待你的判断
                  </h3>
                  <p>先查看证据，再决定如何更新。未经审阅，不会修改文档。</p>
                </div>
                <Button
                  icon={ArrowRight}
                  onClick={() =>
                    openGoal(
                      state.goals.find((g) =>
                        g.patches.some((p) => p.status === "pending"),
                      )?.id || goal.id,
                    )
                  }
                >
                  去审阅
                </Button>
              </section>
              <div className="section-title">
                <div>
                  <h2>我的研究主题</h2>
                  <span>{state.goals.length} 个持续委托</span>
                </div>
                <label className="search">
                  <Search size={16} />
                  <input
                    aria-label="搜索研究主题"
                    placeholder="搜索主题或文档…"
                    value={query}
                    onChange={(e) => setQuery(e.target.value)}
                  />
                </label>
              </div>
              <div className="topic-list">
                {state.goals
                  .filter((g) =>
                    (g.title + g.docTitle)
                      .toLowerCase()
                      .includes(query.toLowerCase()),
                  )
                  .map((g, i) => (
                    <article className="topic" key={g.id}>
                      <div className={`topic-art art-${i % 2}`}>
                        <BookOpen size={26} />
                        <span className="art-line" />
                      </div>
                      <div className="topic-body">
                        <div className="title-row">
                          <button
                            className="topic-link"
                            onClick={() => openGoal(g.id)}
                          >
                            {g.title}
                          </button>
                          {badge(status(g), statusType(g))}
                          {g.paused && badge("计划暂停", "neutral")}
                        </div>
                        <p>
                          {g.section} · {g.sources}
                        </p>
                        <div className="topic-meta">
                          <span>
                            <Clock3 size={14} />
                            {g.frequency}
                          </span>
                          <span>
                            <ShieldCheck size={14} />
                            逐次审阅
                          </span>
                          <span>
                            <FileText size={14} />
                            {g.docTitle}
                          </span>
                        </div>
                      </div>
                      <Button
                        variant={
                          g.patches.some((p) => p.status === "pending")
                            ? "primary-soft"
                            : ""
                        }
                        onClick={() => openGoal(g.id)}
                      >
                        {g.stale
                          ? "重算草稿"
                          : g.patches.some((p) => p.status === "pending")
                            ? "审阅更新"
                            : "打开主题"}
                        <ArrowUpRight size={15} />
                      </Button>
                      <button
                        className="icon-button"
                        aria-label={
                          g.paused ? `恢复 ${g.title}` : `暂停 ${g.title}`
                        }
                        onClick={() => {
                          dispatch({ type: "PAUSE", goalId: g.id });
                          announce(
                            g.paused ? "已恢复后续计划" : "已暂停后续计划",
                          );
                        }}
                      >
                        {g.paused ? <Play size={16} /> : <Pause size={16} />}
                      </button>
                    </article>
                  ))}
              </div>
              {state.goals.every(
                (g) => !(g.title + g.docTitle).includes(query),
              ) && (
                <Empty
                  title="没有匹配的主题"
                  body="换个关键词，或创建一个新的研究委托。"
                />
              )}
              <section className="continuity">
                <div>
                  <Brain size={21} />
                  <h3>研究也会越来越懂你</h3>
                </div>
                <p>筛选范围不合适？从一条建议追溯到偏好，纠正后再继续。</p>
                <button className="text-button" onClick={toMemory}>
                  查看我的记忆 <ArrowRight size={15} />
                </button>
              </section>
            </>
          )}
          {page === "research" && (
            <>
              <div className="page-heading research-heading">
                <div>
                  <button className="back-link" onClick={() => go("home")}>
                    <ArrowLeft size={14} />
                    所有主题
                  </button>
                  <h1>{goal.title}</h1>
                  <p>
                    {goal.section} <span className="dot-separator">·</span>{" "}
                    {badge(status(goal), statusType(goal))}{" "}
                    {goal.paused && badge("计划暂停")}
                  </p>
                </div>
                <div className="actions">
                  <Button
                    icon={goal.paused ? Play : Pause}
                    onClick={() => dispatch({ type: "PAUSE" })}
                  >
                    {goal.paused ? "恢复计划" : "暂停计划"}
                  </Button>
                  <Button
                    icon={RefreshCw}
                    disabled={goal.running}
                    onClick={() => {
                      dispatch({ type: "RUN" });
                      setTab("changes");
                    }}
                  >
                    立即检查
                  </Button>
                </div>
              </div>
              <div className="document-bar">
                <FileText size={18} />
                <strong>{goal.docTitle}</strong>
                <span>当前 v{goal.version}</span>
                <button
                  className="text-button"
                  onClick={() => setTab("document")}
                >
                  查看文档 <ArrowUpRight size={14} />
                </button>
              </div>
              <div className="tabs" role="tablist" aria-label="研究内容">
                {[
                  ["changes", "本次更新", pending.length],
                  ["document", "研究文档"],
                  ["sources", "来源与依据"],
                  ["activity", "活动记录"],
                ].map(([key, title, count]) => (
                  <button
                    role="tab"
                    aria-selected={tab === key}
                    key={key}
                    onClick={() => setTab(key)}
                    className={tab === key ? "active" : ""}
                  >
                    {title}
                    {count > 0 && <span>{count}</span>}
                  </button>
                ))}
              </div>
              {goal.running && (
                <div className="banner purple">
                  <Loader2 className="spin" size={18} />
                  <div>
                    <strong>正在核查来源，重新整理草稿</strong>
                    <p>原有文档保持不变。完成后请审阅新差异。</p>
                  </div>
                  <Button onClick={() => dispatch({ type: "CANCEL" })}>
                    取消本次
                  </Button>
                </div>
              )}
              {(goal.stale || conflict) && !goal.running && (
                <div className="banner warning">
                  <CircleAlert size={20} />
                  <div>
                    <strong>
                      {conflict
                        ? "文档已被其他协作者修改"
                        : "偏好已变更，这份草稿需要重算"}
                    </strong>
                    <p>
                      {conflict
                        ? `草稿基于 v${goal.baseVersion}，当前文档为 v${goal.version}。刷新后重新审阅。`
                        : "更正已经生效，旧建议暂不可发布。已发布的内容不受影响。"}
                    </p>
                  </div>
                  <Button
                    onClick={() =>
                      dispatch({
                        type: "RUN",
                        reason: "基于最新版本与偏好重算草稿",
                      })
                    }
                    icon={RefreshCw}
                  >
                    重算草稿
                  </Button>
                </div>
              )}
              {(!goal.grant || expired) && (
                <div className="banner warning">
                  <ShieldCheck size={19} />
                  <div>
                    <strong>
                      {expired ? "文档写入授权已到期" : "文档写入授权已撤销"}
                    </strong>
                    <p>可以继续整理草稿，重新授权后才能应用更新。</p>
                  </div>
                  <Button onClick={() => go("settings")}>管理授权</Button>
                </div>
              )}
              {tab === "changes" && (
                <>
                  {goal.patches.length === 0 ? (
                    <Empty
                      title="本轮没有新的变化"
                      body="可立即检查来源，生成一份模拟研究草稿。"
                    >
                      <Button
                        icon={RefreshCw}
                        onClick={() => dispatch({ type: "RUN" })}
                      >
                        开始检查
                      </Button>
                    </Empty>
                  ) : (
                    <div className="review-grid">
                      <aside className="change-list">
                        <div className="list-heading">
                          <h3>变化清单</h3>
                          <span>{goal.patches.length} 条</span>
                        </div>
                        {goal.patches.map((p) => (
                          <div
                            key={p.id}
                            className={`change-item ${patch?.id === p.id ? "active" : ""}`}
                          >
                            <div className="change-top">
                              {p.status === "pending" ? (
                                <input
                                  type="checkbox"
                                  aria-label={`选择 ${p.title}`}
                                  checked={p.selected}
                                  onChange={(e) =>
                                    dispatch({
                                      type: "SELECT",
                                      id: p.id,
                                      value: e.target.checked,
                                    })
                                  }
                                />
                              ) : (
                                <CheckCircle2
                                  size={17}
                                  className={
                                    p.status === "published"
                                      ? "success"
                                      : "muted"
                                  }
                                />
                              )}
                              <button onClick={() => setActivePatch(p.id)}>
                                {p.title}
                              </button>
                            </div>
                            <div className="change-meta">
                              <span>{p.section}</span>
                              {badge(
                                {
                                  pending: p.kind,
                                  published: "已应用",
                                  deferred: "暂缓",
                                  rejected: "不采用",
                                  unchanged: "无变化",
                                }[p.status],
                                p.status === "published"
                                  ? "green"
                                  : p.kind === "待判断"
                                    ? "amber"
                                    : "neutral",
                              )}
                            </div>
                          </div>
                        ))}
                        <div className="preference-link">
                          <Brain size={18} />
                          <p>
                            为什么这样推荐？<small>查看本次采用的偏好</small>
                          </p>
                          <button
                            className="icon-button"
                            aria-label="查看本次采用的偏好"
                            onClick={toMemory}
                          >
                            <ArrowUpRight size={17} />
                          </button>
                        </div>
                      </aside>
                      {patch && (
                        <section className="diff-panel">
                          <div className="diff-title">
                            <div>
                              {badge(
                                patch.kind,
                                patch.kind === "待判断" ? "amber" : "purple",
                              )}
                              <h2>{patch.section}</h2>
                            </div>
                            {patch.status === "pending" && (
                              <Button
                                icon={Pencil}
                                onClick={() =>
                                  setOverlay({
                                    type: "editPatch",
                                    id: patch.id,
                                    text: patch.after,
                                  })
                                }
                              >
                                编辑
                              </Button>
                            )}
                          </div>
                          <div className="diff-columns">
                            <div className="before">
                              <div className="diff-label">
                                原文 <span>v{goal.version}</span>
                              </div>
                              <p>{patch.before || "此章节尚无内容。"}</p>
                            </div>
                            <div className="after">
                              <div className="diff-label">
                                建议内容{" "}
                                <span>
                                  {patch.status === "published"
                                    ? "已应用"
                                    : "待审阅"}
                                </span>
                              </div>
                              <p>{patch.after}</p>
                            </div>
                          </div>
                          <div className="rationale">
                            <Sparkles size={16} />
                            <p>{patch.reason}</p>
                          </div>
                          {patch.memoryId && (
                            <div className="inline-memory">
                              <Brain size={17} />
                              <div>
                                <strong>本次采用的偏好</strong>
                                <p>{effectivePreference(state, goal.id)}</p>
                              </div>
                              <Button onClick={toMemory}>
                                纠正偏好
                                <ArrowRight size={14} />
                              </Button>
                            </div>
                          )}
                          <div className="diff-actions">
                            {patch.status === "pending" ? (
                              <>
                                <button
                                  className="text-button"
                                  onClick={() =>
                                    dispatch({ type: "DEFER", id: patch.id })
                                  }
                                >
                                  暂缓处理
                                </button>
                                <button
                                  className="text-button"
                                  onClick={() =>
                                    dispatch({
                                      type: "DEFER",
                                      id: patch.id,
                                      reject: true,
                                    })
                                  }
                                >
                                  本次不采用
                                </button>
                              </>
                            ) : ["deferred", "rejected"].includes(
                                patch.status,
                              ) ? (
                              <Button
                                onClick={() =>
                                  dispatch({ type: "RESTORE", id: patch.id })
                                }
                              >
                                重新审阅
                              </Button>
                            ) : (
                              <span className="success">
                                <CheckCircle2 size={16} />
                                {patch.status === "published"
                                  ? "这条更新已写入本地文档"
                                  : "与当前文档一致"}
                              </span>
                            )}
                          </div>
                        </section>
                      )}
                      <aside className="evidence-panel">
                        <div className="list-heading">
                          <h3>来源依据</h3>
                          <Link2 size={16} />
                        </div>
                        <span className="source-number">01</span>
                        <h4>{patch?.source.name}</h4>
                        <p className="source-caption">{patch?.source.label}</p>
                        <blockquote>{patch?.source.excerpt}</blockquote>
                        <p className="source-time">
                          <Clock3 size={13} />
                          {patch?.source.date}
                        </p>
                        <button
                          className="text-button"
                          onClick={() =>
                            setOverlay({ type: "source", source: patch.source })
                          }
                        >
                          查看完整快照 <ArrowUpRight size={14} />
                        </button>
                        <div className="evidence-note">
                          演示材料用于体验审阅流程，不代表实时产品动态。
                        </div>
                      </aside>
                    </div>
                  )}
                  <div className="publish-bar">
                    <div>
                      <span className="selection-count">{chosen.length}</span>
                      <div>
                        <strong>
                          {chosen.length ? "条更新已选中" : "选择要应用的更新"}
                        </strong>
                        <small>仅更新「{goal.section}」· 应用前再次确认</small>
                      </div>
                    </div>
                    <Button
                      variant="primary"
                      icon={Check}
                      disabled={!chosen.length || blocked}
                      onClick={() =>
                        setOverlay({
                          type: "publish",
                          revision: state.revision,
                        })
                      }
                    >
                      审阅并应用 {chosen.length || ""} 条
                    </Button>
                  </div>
                </>
              )}
              {tab === "document" && (
                <section className="document-preview">
                  <header>
                    <span>
                      {badge(`v${goal.version}`, "purple")}
                      <small>本地模拟文档</small>
                    </span>
                    <Button icon={Download} onClick={download}>
                      导出 Markdown
                    </Button>
                  </header>
                  <h1>{goal.docTitle}</h1>
                  <p className="document-intro">
                    持续整理产品机制、长期记忆与可控执行。
                  </p>
                  {Object.entries(goal.document).map(([key, value]) => (
                    <section key={key}>
                      <h2>
                        {{
                          scope: "执行与授权",
                          memory: "长期记忆",
                          range: "产品筛选",
                        }[key] || key}
                      </h2>
                      <p>{value}</p>
                    </section>
                  ))}
                  {!Object.keys(goal.document).length && (
                    <Empty
                      title="还没有发布的内容"
                      body="审阅首版草稿后，在这里查看完整文档。"
                    />
                  )}
                </section>
              )}
              {tab === "sources" && (
                <section className="sources-view">
                  <h2>本轮研究依据</h2>
                  <p className="muted">
                    以下是固定的模拟来源快照，原型不会访问外部网站。
                  </p>
                  {goal.patches.map((p) => (
                    <button
                      key={p.id}
                      className="source-row"
                      onClick={() =>
                        setOverlay({ type: "source", source: p.source })
                      }
                    >
                      <Link2 size={20} />
                      <span>
                        <strong>{p.source.name}</strong>
                        <small>
                          {p.source.date} · 关联「{p.section}」
                        </small>
                      </span>
                      <ArrowUpRight size={17} />
                    </button>
                  ))}
                </section>
              )}
              {tab === "activity" && (
                <section className="activity">
                  <h2>每一次变化，都有记录</h2>
                  {goal.activities.map((a, i) => (
                    <div className="activity-row" key={a.id}>
                      <span
                        className={`activity-dot ${i === 0 ? "latest" : ""}`}
                      />
                      <div>
                        <strong>{a.text}</strong>
                        <small>{a.time} · 本地模拟</small>
                      </div>
                    </div>
                  ))}
                </section>
              )}
              <div className="simulation">
                <button
                  className="text-button"
                  onClick={() => setSimOpen(!simOpen)}
                >
                  <FlaskConical size={15} />
                  演示场景
                  <ChevronRight size={14} className={simOpen ? "rotate" : ""} />
                </button>
                {simOpen && (
                  <div className="simulation-actions">
                    <Button
                      onClick={() => {
                        dispatch({ type: "CONFLICT" });
                        setTab("changes");
                        announce("已模拟其他协作者修改文档");
                      }}
                    >
                      模拟版本冲突
                    </Button>
                    <Button
                      onClick={() => dispatch({ type: "GRANT", value: false })}
                    >
                      模拟撤销授权
                    </Button>
                    <span>仅改变本地模拟状态</span>
                  </div>
                )}
              </div>
            </>
          )}
          {page === "memory" && (
            <>
              <div className="page-heading">
                <div>
                  <h1>让下一次，更懂你。</h1>
                  <p>看见记忆的出处，也能改变它。你始终拥有最后的决定权。</p>
                </div>
                <Button
                  icon={ArrowLeft}
                  onClick={() => {
                    go("research");
                    setTab("changes");
                  }}
                >
                  回到研究草稿
                </Button>
              </div>
              <div className="memory-toolbar">
                <label className="search">
                  <Search size={16} />
                  <input
                    aria-label="搜索记忆"
                    placeholder="搜索偏好、来源或内容…"
                    value={query}
                    onChange={(e) => setQuery(e.target.value)}
                  />
                </label>
                <div className="filter-buttons">
                  {[
                    ["all", "全部"],
                    ["用户确认", "已确认"],
                    ["自动提取", "自动提取"],
                    ["inactive", "已停用"],
                  ].map(([key, name]) => (
                    <button
                      key={key}
                      className={filter === key ? "active" : ""}
                      onClick={() => setFilter(key)}
                    >
                      {name}
                    </button>
                  ))}
                </div>
                <span className="muted">当前研究：{goal.title}</span>
              </div>
              <div className="memory-grid">
                <aside className="memory-list">
                  {state.memories
                    .filter(
                      (m) =>
                        (m.title + m.text).includes(query) &&
                        (filter === "all" ||
                          (filter === "inactive"
                            ? m.status === "inactive"
                            : m.type === filter)),
                    )
                    .map((m) => (
                      <button
                        className={`memory-item ${memory?.id === m.id ? "active" : ""}`}
                        key={m.id}
                        onClick={() => {
                          setMemoryId(m.id);
                          setPreview(false);
                        }}
                      >
                        <div>
                          {badge(
                            m.status === "inactive" ? "已停用" : m.type,
                            m.status === "inactive"
                              ? "neutral"
                              : m.type === "用户确认"
                                ? "purple"
                                : "blue",
                          )}
                          <ChevronRight size={15} />
                        </div>
                        <h3>{m.title}</h3>
                        <p>{m.text}</p>
                        <small>
                          {m.scope === "all"
                            ? "全部研究"
                            : state.goals.find((g) => g.id === m.scope)
                                ?.title || "指定研究"}
                        </small>
                      </button>
                    ))}
                  {state.memories.filter(
                    (m) =>
                      (m.title + m.text).includes(query) &&
                      (filter === "all" ||
                        (filter === "inactive"
                          ? m.status === "inactive"
                          : m.type === filter)),
                  ).length === 0 && (
                    <Empty title="没有匹配的记忆" body="尝试其他筛选条件。" />
                  )}
                  <p className="memory-footnote">
                    删除记录 {state.tombstones.length} 条 · 原始资料保持不变
                  </p>
                </aside>
                {memory ? (
                  <section className="memory-detail">
                    <div className="memory-detail-head">
                      <div>
                        <Brain size={22} />
                        <h2>{memory.title}</h2>
                      </div>
                      {badge(
                        memory.status === "active" ? "使用中" : "已停用",
                        memory.status === "active" ? "green" : "neutral",
                      )}
                    </div>
                    <div className="memory-quote">{memory.text}</div>
                    <div className="detail-meta">
                      <span>
                        适用范围{" "}
                        <strong>
                          {memory.scope === "all"
                            ? "全部研究"
                            : state.goals.find((g) => g.id === memory.scope)
                                ?.title}
                        </strong>
                      </span>
                      <span>
                        确认版本 <strong>v{memory.revision}</strong>
                      </span>
                    </div>
                    <section className="origin">
                      <h3>
                        <Link2 size={16} />
                        这条记忆从哪里来
                      </h3>
                      <blockquote>“{memory.quote}”</blockquote>
                      <button
                        className="text-button"
                        onClick={() =>
                          setOverlay({
                            type: "source",
                            source: {
                              name: memory.origin,
                              excerpt: memory.quote,
                              date: "2026-10-27 14:32",
                              label: "模拟会话记录",
                            },
                          })
                        }
                      >
                        {memory.origin}
                        <ArrowUpRight size={14} />
                      </button>
                    </section>
                    <section className="impact">
                      <h3>
                        <FileText size={16} />
                        会影响什么
                      </h3>
                      <p>
                        {memory.scope === "all"
                          ? "适用于全部研究的后续草稿。"
                          : "适用于当前主题的筛选与写作。"}
                        更改后，相关草稿需要重新整理。
                      </p>
                      <button
                        className="impact-link"
                        onClick={() =>
                          openGoal(
                            memory.scope === "all" ? goal.id : memory.scope,
                          )
                        }
                      >
                        <FileText size={19} />
                        <span>
                          {state.goals.find(
                            (g) =>
                              g.id ===
                              (memory.scope === "all" ? goal.id : memory.scope),
                          )?.docTitle || goal.docTitle}
                          <small>
                            {state.goals.some((g) => g.stale)
                              ? "受影响草稿待重算"
                              : "可查看当前草稿与采用情况"}
                          </small>
                        </span>
                        <ArrowRight size={17} />
                      </button>
                    </section>
                    {memory.sync === "pending" ? (
                      <div className="banner purple">
                        <Loader2 className="spin" size={17} />
                        <div>
                          <strong>后续使用已生效</strong>
                          <p>长期记忆正在同步；本地覆盖已生效。</p>
                        </div>
                      </div>
                    ) : (
                      memory.revision > 1 && (
                        <div className="inline-success">
                          <CheckCircle2 size={17} />
                          已生效 · 长期记忆同步完成
                        </div>
                      )
                    )}
                    <div className="memory-controls">
                      <Button
                        variant="primary"
                        icon={Pencil}
                        onClick={() => {
                          if (memory.scope !== "all") setGoalId(memory.scope);
                          setOverlay({
                            type: "memory",
                            id: memory.id,
                            text: memory.text,
                            scope: memory.scope,
                          });
                        }}
                      >
                        纠正这条记忆
                      </Button>
                      <Button
                        onClick={() => {
                          dispatch({
                            type: "MEMORY_STATUS",
                            id: memory.id,
                            status:
                              memory.status === "active"
                                ? "inactive"
                                : "active",
                          });
                          announce(
                            memory.status === "active"
                              ? "已停用，相关草稿需要重算"
                              : "已启用，相关草稿需要重算",
                          );
                        }}
                      >
                        {memory.status === "active" ? "停用" : "重新启用"}
                      </Button>
                      <button
                        className="icon-button danger"
                        aria-label="删除记忆"
                        onClick={() =>
                          setOverlay({ type: "deleteMemory", id: memory.id })
                        }
                      >
                        <Trash2 size={17} />
                      </button>
                    </div>
                    <div className="memory-secondary">
                      {memory.id === "range" && (
                        <button
                          className="text-button"
                          onClick={() => {
                            dispatch({ type: "IGNORE_ONCE" });
                            openGoal(goal.id);
                            setActivePatch("range");
                            announce("仅调整本次建议，长期记忆未改变");
                          }}
                        >
                          仅本次不采用
                        </button>
                      )}
                      <button
                        className="text-button"
                        onClick={() => setPreview(!preview)}
                      >
                        <FlaskConical size={15} />
                        用一个例子试试
                      </button>
                    </div>
                    {preview && (
                      <div className="try-example">
                        <strong>
                          一个发布已半年、上周仍更新的产品，是否纳入？
                        </strong>
                        <p>
                          {memory.id === "range"
                            ? memory.status === "inactive"
                              ? "当前时间偏好已停用，需结合研究目标判断。"
                              : memory.text.includes("两个月")
                                ? "可以纳入。当前规则关注近期活跃，不以首次发布时间排除。"
                                : "按当前规则可能被排除；如需关注持续活跃产品，请纠正时间范围。"
                            : `当前将采用：${memory.status === "active" ? memory.text : "此条记忆已停用。"}`}
                        </p>
                        <small>固定示例反馈 · 未调用模型</small>
                      </div>
                    )}
                    <p className="quiet-note">
                      更改记忆不会直接修改已经发布的文档。
                    </p>
                  </section>
                ) : (
                  <Empty
                    title="记忆已清空"
                    body="可通过重置演示重新体验记忆纠错流程。"
                  />
                )}
              </div>
            </>
          )}
          {page === "settings" && (
            <>
              <div className="page-heading">
                <div>
                  <h1>授权有边界，委托才安心。</h1>
                  <p>管理每个主题可以读取和更新的范围。</p>
                </div>
              </div>
              <label className="field settings-picker">
                当前研究
                <select
                  value={goal.id}
                  onChange={(e) => setGoalId(e.target.value)}
                >
                  {state.goals.map((g) => (
                    <option key={g.id} value={g.id}>
                      {g.title}
                    </option>
                  ))}
                </select>
              </label>
              <div className="settings-grid">
                <section className="settings-panel">
                  <h2>
                    <ShieldCheck size={21} />
                    文档写入权限
                  </h2>
                  <div className="permission-state">
                    {badge(
                      goal.grant ? "已授权 · 逐次审阅" : "已撤销",
                      goal.grant ? "green" : "amber",
                    )}
                    <p>
                      {goal.docTitle} / {goal.section}
                    </p>
                  </div>
                  <p className="muted">
                    批准仅用于本次选中的差异，不自动扩展到其他文档或后续更新。
                  </p>
                  <Button
                    variant={goal.grant ? "danger-outline" : "primary"}
                    onClick={() =>
                      setOverlay({ type: "grant", value: !goal.grant })
                    }
                  >
                    {goal.grant ? "撤销写入授权" : "重新授权"}
                  </Button>
                </section>
                <section className="settings-panel">
                  <h2>
                    <Clock3 size={21} />
                    运行计划
                  </h2>
                  <SettingsForm
                    key={goal.id + goal.expiry + goal.frequency}
                    goal={goal}
                    onSave={(values) => {
                      dispatch({ type: "SETTINGS", values });
                      announce("委托设置已保存");
                    }}
                  />
                  <p className="quiet-note">
                    原型不在后台定时执行。点击“立即检查”体验一次运行。
                  </p>
                </section>
              </div>
              <section className="settings-panel full">
                <h2>
                  <Link2 size={20} />
                  允许读取的来源
                </h2>
                <p>{goal.sources}</p>
                <p className="muted">
                  此体验版使用固定快照，不读取网站、GitHub 或飞书账户。
                </p>
              </section>
            </>
          )}
        </main>
        <footer className="app-footer">
          <span>知续 · 让每一次研究，都成为下一次的起点。</span>
          <span>本地原型 / 无后端连接</span>
        </footer>
      </div>
      {toast && (
        <div role="status" className="toast">
          <CheckCircle2 size={18} />
          {toast}
        </div>
      )}
      {overlay?.type === "new" && (
        <Overlay
          title="新建研究委托"
          subtitle="交代一次，持续跟踪。你来决定哪些变化值得写入。"
          onClose={() => setOverlay(null)}
        >
          <NewGoal
            onCancel={() => setOverlay(null)}
            onSave={(values) => {
              const next = reduceState(state, { type: "CREATE", values });
              setState(next);
              setGoalId(next.goals[0].id);
              setOverlay(null);
              go("research");
              setTab("changes");
              announce("委托已创建，正在生成首版草稿");
            }}
          />
        </Overlay>
      )}
      {overlay?.type === "editPatch" && (
        <Overlay
          title="编辑建议内容"
          subtitle="修改后需重新审阅；原文暂不改变。"
          onClose={() => setOverlay(null)}
        >
          <form
            onSubmit={(e) => {
              e.preventDefault();
              dispatch({
                type: "EDIT_PATCH",
                id: overlay.id,
                text: overlay.text.trim(),
              });
              setOverlay(null);
              announce("建议已保存");
            }}
          >
            <label className="field">
              建议内容
              <textarea
                rows={10}
                required
                value={overlay.text}
                onChange={(e) =>
                  setOverlay({ ...overlay, text: e.target.value })
                }
              />
            </label>
            <div className="form-footer">
              <Button type="button" onClick={() => setOverlay(null)}>
                取消
              </Button>
              <Button variant="primary" disabled={!overlay.text.trim()}>
                保存建议
              </Button>
            </div>
          </form>
        </Overlay>
      )}
      {overlay?.type === "memory" && (
        <Overlay
          title="纠正这条记忆"
          subtitle="明确新规则和适用范围，相关草稿会重新整理。"
          onClose={() => setOverlay(null)}
        >
          <form
            onSubmit={(e) => {
              e.preventDefault();
              dispatch({
                type: "SAVE_MEMORY",
                id: overlay.id,
                text: overlay.text,
                scope: overlay.scope,
              });
              dispatch({ type: "RUN", reason: "按已纠正的记忆重新整理草稿" });
              setOverlay(null);
              announce("记忆已纠正，正在重算当前草稿");
            }}
          >
            <label className="field">
              新的偏好
              <textarea
                required
                rows={5}
                value={overlay.text}
                onChange={(e) =>
                  setOverlay({ ...overlay, text: e.target.value })
                }
              />
            </label>
            {overlay.id === "range" && (
              <button
                type="button"
                className="suggested-rule"
                onClick={() =>
                  setOverlay({ ...overlay, text: suggestedPreference })
                }
              >
                <Sparkles size={16} />
                <span>试用示例：优先近两个月活跃，保留仍更新的老产品</span>
                <Plus size={16} />
              </button>
            )}
            <fieldset>
              <legend>作用范围</legend>
              <label className="radio-row">
                <input
                  type="radio"
                  name="scope"
                  checked={overlay.scope === goal.id}
                  onChange={() => setOverlay({ ...overlay, scope: goal.id })}
                />
                当前研究 <span>{goal.title}</span>
              </label>
              <label className="radio-row">
                <input
                  type="radio"
                  name="scope"
                  checked={overlay.scope === "all"}
                  onChange={() => setOverlay({ ...overlay, scope: "all" })}
                />
                全部研究
              </label>
            </fieldset>
            <div className="banner purple">
              <Brain size={19} />
              <p>
                保存后立即用于后续处理。受影响的草稿须重新审阅，已发布文档保持不变。
              </p>
            </div>
            <div className="form-footer">
              <Button type="button" onClick={() => setOverlay(null)}>
                取消
              </Button>
              <Button variant="primary" disabled={!overlay.text.trim()}>
                保存并重算草稿
              </Button>
            </div>
          </form>
        </Overlay>
      )}
      {overlay?.type === "publish" && (
        <Overlay
          compact
          title="确认应用这些更新？"
          subtitle="更新的是本地模拟文档，不会写入真实飞书。"
          onClose={() => setOverlay(null)}
        >
          <div className="confirm-target">
            <FileText size={22} />
            <div>
              <strong>{goal.docTitle}</strong>
              <p>
                {goal.section} · v{goal.version} → v{goal.version + 1}
              </p>
            </div>
          </div>
          <ul className="confirm-list">
            {chosen.map((p) => (
              <li key={p.id}>
                <Check size={16} />
                {p.title}
              </li>
            ))}
          </ul>
          {(blocked || overlay.revision !== state.revision) && (
            <div className="banner warning">
              内容或状态已变化，请关闭后重新审阅。
            </div>
          )}
          <div className="form-footer">
            <Button onClick={() => setOverlay(null)}>继续审阅</Button>
            <Button
              variant="primary"
              disabled={
                blocked || !chosen.length || overlay.revision !== state.revision
              }
              onClick={() => {
                dispatch({ type: "PUBLISH", revision: overlay.revision });
                setOverlay(null);
                setTab("document");
                announce("更新已应用，本地文档已核验");
              }}
            >
              确认应用 {chosen.length} 条
            </Button>
          </div>
        </Overlay>
      )}
      {overlay?.type === "source" && (
        <Overlay
          title={overlay.source.name}
          subtitle={overlay.source.label}
          onClose={() => setOverlay(null)}
        >
          <div className="source-snapshot">
            <span>
              <Clock3 size={15} />
              {overlay.source.date}
            </span>
            <h3>来源内容</h3>
            <blockquote>{overlay.source.excerpt}</blockquote>
            <p className="muted">
              这是原型内置的来源片段，用于演示引用核查。没有连接或抓取真实账户数据。
            </p>
          </div>
        </Overlay>
      )}
      {overlay?.type === "deleteMemory" && (
        <Overlay
          compact
          title="删除这条记忆？"
          subtitle="不删除原始会话或研究文档。"
          onClose={() => setOverlay(null)}
        >
          <p>
            删除后停止使用，并阻止同一旧来源重新提取此项。相关草稿会标记为待重算。
          </p>
          <div className="banner warning">
            <Trash2 size={18} />
            <span>此操作不可撤销；演示数据可通过“重置演示”恢复。</span>
          </div>
          <div className="form-footer">
            <Button onClick={() => setOverlay(null)}>保留记忆</Button>
            <Button
              variant="danger-solid"
              onClick={() => {
                dispatch({
                  type: "MEMORY_STATUS",
                  id: overlay.id,
                  status: "deleted",
                });
                setOverlay(null);
                announce("记忆已删除，旧来源的再次提取已抑制");
              }}
            >
              删除并防再次提取
            </Button>
          </div>
        </Overlay>
      )}
      {overlay?.type === "grant" && (
        <Overlay
          compact
          title={overlay.value ? "授权指定文档更新" : "撤销文档写入授权"}
          subtitle={goal.docTitle + " / " + goal.section}
          onClose={() => setOverlay(null)}
        >
          <p>
            {overlay.value
              ? `允许审阅后应用所选差异，有效期至 ${goal.expiry}。不会授予其他文档权限。`
              : "后续文档更新将停止，仍可读取模拟来源并生成草稿。已经应用的内容不会回滚。"}
          </p>
          <div className="form-footer">
            <Button onClick={() => setOverlay(null)}>取消</Button>
            <Button
              variant={overlay.value ? "primary" : "danger-solid"}
              onClick={() => {
                dispatch({ type: "GRANT", value: overlay.value });
                setOverlay(null);
                announce("授权设置已更新");
              }}
            >
              {overlay.value ? "确认授权" : "确认撤销"}
            </Button>
          </div>
        </Overlay>
      )}
      {overlay?.type === "reset" && (
        <Overlay
          compact
          title="重置本地演示？"
          subtitle="你在此原型中的文档更新、委托和记忆修改将清空。"
          onClose={() => setOverlay(null)}
        >
          <div className="form-footer">
            <Button onClick={() => setOverlay(null)}>取消</Button>
            <Button
              variant="primary"
              onClick={() => {
                setState(initialState());
                setGoalId("personal");
                setMemoryId("range");
                setActivePatch("scope");
                setTab("changes");
                setOverlay(null);
                go("home");
                announce("已恢复初始演示数据");
              }}
            >
              确认重置
            </Button>
          </div>
        </Overlay>
      )}
      {overlay?.type === "about" && (
        <Overlay compact title="本地体验空间" onClose={() => setOverlay(null)}>
          <p>
            所有研究、文档发布和记忆处理均为模拟。数据保存在当前浏览器，不发送至服务器；刷新可继续体验。
          </p>
          <p className="muted">
            关闭页面后不会继续运行定时任务。再次打开时会恢复本地状态。
          </p>
        </Overlay>
      )}
    </div>
  );
}
function NewGoal({ onSave, onCancel }) {
  const [values, set] = useState({
    title: "",
    docTitle: "",
    section: "产品机制与技术内核",
    sources: "产品官网、GitHub 官方仓库",
    frequency: "每周一 09:00",
    expiry: "2026-12-03",
    budget: "20",
  });
  const update = (key, value) => set({ ...values, [key]: value });
  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        onSave(
          Object.fromEntries(
            Object.entries(values).map(([k, v]) => [k, v.trim()]),
          ),
        );
      }}
    >
      <label className="field">
        研究主题
        <input
          autoFocus
          required
          maxLength={60}
          placeholder="例如：持续跟踪 Personal Agent 产品"
          value={values.title}
          onChange={(e) => update("title", e.target.value)}
        />
      </label>
      <label className="field">
        来源范围
        <textarea
          required
          rows={2}
          value={values.sources}
          onChange={(e) => update("sources", e.target.value)}
        />
      </label>
      <label className="field">
        目标文档
        <input
          required
          placeholder="输入模拟文档名称"
          value={values.docTitle}
          onChange={(e) => update("docTitle", e.target.value)}
        />
      </label>
      <label className="field">
        允许更新的章节
        <input
          required
          value={values.section}
          onChange={(e) => update("section", e.target.value)}
        />
      </label>
      <div className="form-grid">
        <label className="field">
          检查周期
          <select
            value={values.frequency}
            onChange={(e) => update("frequency", e.target.value)}
          >
            <option>每周一 09:00</option>
            <option>每个工作日 09:00</option>
            <option>仅手动执行</option>
          </select>
        </label>
        <label className="field">
          到期日
          <input
            type="date"
            required
            min={new Date().toISOString().slice(0, 10)}
            value={values.expiry}
            onChange={(e) => update("expiry", e.target.value)}
          />
        </label>
        <label className="field">
          每月预算（元）
          <input
            type="number"
            min="1"
            max="1000"
            required
            value={values.budget}
            onChange={(e) => update("budget", e.target.value)}
          />
        </label>
      </div>
      <div className="banner purple">
        <ShieldCheck size={19} />
        <div>
          <strong>逐次审阅 · 默认开启</strong>
          <p>生成草稿不等于发布。每次写入都需要你确认。</p>
        </div>
      </div>
      <div className="form-footer">
        <Button type="button" onClick={onCancel}>
          取消
        </Button>
        <Button
          variant="primary"
          disabled={
            !values.title.trim() ||
            !values.docTitle.trim() ||
            !values.sources.trim() ||
            !values.section.trim()
          }
        >
          保存并生成草稿
        </Button>
      </div>
    </form>
  );
}
function SettingsForm({ goal, onSave }) {
  const [frequency, setF] = useState(goal.frequency),
    [expiry, setE] = useState(goal.expiry),
    [budget, setB] = useState(goal.budget);
  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        onSave({ frequency, expiry, budget });
      }}
    >
      <label className="field">
        检查周期
        <select value={frequency} onChange={(e) => setF(e.target.value)}>
          <option>每周一 09:00</option>
          <option>每个工作日 09:00</option>
          <option>仅手动执行</option>
        </select>
      </label>
      <div className="form-grid">
        <label className="field">
          到期日
          <input
            type="date"
            required
            value={expiry}
            onChange={(e) => setE(e.target.value)}
          />
        </label>
        <label className="field">
          月预算（元）
          <input
            type="number"
            min="1"
            max="1000"
            required
            value={budget}
            onChange={(e) => setB(e.target.value)}
          />
        </label>
      </div>
      <Button variant="primary-soft">保存设置</Button>
    </form>
  );
}
createRoot(document.getElementById("root")).render(<App />);
