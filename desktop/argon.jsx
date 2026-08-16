// Argon desktop widget for Übersicht <https://tracesof.net/uebersicht/>.
//
// Renders the view model argon-widget.py builds — the same object SwiftBar
// renders, so the two readouts cannot disagree. Everything needing a clock
// (countdowns, "overdue 2d", "running 12m") arrives as a finished string.
//
// The look is lifted from the iOS app rather than invented: colours are
// ArgonPalette, the card is argonGlassPanel, the serif display face is
// Font.argonDisplay, and the Overdue/Today/Later sections match
// ArgonDashboardView. Changing a colour means changing PALETTE in the Python.

import { run } from "uebersicht";

const SCRIPT = "$HOME/.config/argon/argon-widget.py";

export const command = SCRIPT + " --json";

// Actions shell out to the same script SwiftBar's menu items call, which posts
// to the same HTTP surface the iOS app uses — so a task completed here gets the
// daily-log and habit side effects that live in Argon's own tool classes.
const sh = (s) => "'" + String(s).replace(/'/g, "'\\''") + "'";

// Dim the row immediately. The next poll (5s) replaces the DOM with real data,
// which is precisely when the dimming should stop — so nothing has to undo it.
const act = (event, ...args) => {
  const row = event.currentTarget.closest(".task, .actions");
  if (row) row.classList.add("pending");
  run(SCRIPT + " --do " + args.map(sh).join(" "));
};

// Übersicht polls locally; the server caches the Google round-trip, so a short
// interval costs one LAN request — see TASKS_TTL_S in argon/api/server.py.
export const refreshFrequency = 30000;

export const className = `
  top: 24px; right: 24px;
  width: 344px;
  font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", sans-serif;
  font-size: 12px;
  color: #F4F8FF;
  -webkit-font-smoothing: antialiased;

  /* ArgonBackdrop: canvas gradient, electric-blue bloom top-trailing,
     cobalt wash bottom-leading. */
  background:
    radial-gradient(120% 90% at 100% 0%, rgba(93,169,255,0.20), rgba(93,169,255,0.04) 45%, transparent 70%),
    radial-gradient(110% 80% at 0% 100%, rgba(39,93,255,0.12), transparent 65%),
    linear-gradient(135deg, #040812 0%, #081326 50%, #040812 100%);
  border-radius: 28px;
  border: 1px solid rgba(255,255,255,0.10);
  box-shadow: 0 22px 44px rgba(0,0,0,0.55), inset 0 1px 0 rgba(255,255,255,0.06);
  padding: 20px;
  overflow: hidden;

  /* Base for every glyph, so an icon can never render as an unstyled black
     blob just because its container forgot a rule. */
  svg { fill: none; stroke: currentColor; stroke-width: 1.9;
        stroke-linecap: round; stroke-linejoin: round; }

  .eyebrow { font-size: 10px; font-weight: 700; letter-spacing: 1.8px;
             color: #A9DDFF; text-transform: uppercase; }
  .display { font-family: ui-serif, Georgia, "Times New Roman", serif;
             font-weight: 600; color: #F4F8FF; }

  .hero { display: flex; align-items: flex-start; gap: 14px; }
  .hero .title { font-size: 23px; line-height: 1.18; margin-top: 5px;
                 display: -webkit-box; -webkit-line-clamp: 2;
                 -webkit-box-orient: vertical; overflow: hidden; }
  .hero .caption { font-size: 11px; color: #9BAAC0; margin-top: 5px; }

  /* ArgonStatusCard's mode badge: blurred halo behind a hairline circle. */
  .orb { position: relative; flex: 0 0 auto; width: 42px; height: 42px;
         border-radius: 50%; background: rgba(255,255,255,0.055);
         border: 1px solid rgba(93,169,255,0.28);
         display: flex; align-items: center; justify-content: center; }
  .orb::before { content: ""; position: absolute; inset: -6px; border-radius: 50%;
                 background: rgba(93,169,255,0.15); filter: blur(7px); z-index: -1; }
  .orb svg { width: 17px; height: 17px; stroke: #A9DDFF; fill: none;
             stroke-width: 1.9; stroke-linecap: round; stroke-linejoin: round; }

  .metrics { display: flex; gap: 8px; margin-top: 16px; }
  .metric { flex: 1; padding: 10px 11px; border-radius: 15px;
            background: rgba(0,0,0,0.19); border: 1px solid rgba(255,255,255,0.055); }
  .metric svg { width: 11px; height: 11px; stroke: #A9DDFF; fill: none;
                stroke-width: 2; stroke-linecap: round; stroke-linejoin: round; }
  .metric .value { font-size: 18px; margin-top: 5px; }
  .metric .label { font-size: 8px; font-weight: 700; letter-spacing: 1px;
                   color: #9BAAC0; margin-top: 2px; }

  .alert { display: flex; gap: 8px; align-items: flex-start; margin-top: 14px;
           padding: 10px 12px; border-radius: 14px; font-size: 11px;
           color: #FF9F45; background: rgba(255,159,69,0.09);
           border: 1px solid rgba(255,159,69,0.22); }

  /* Today's plan — the blocks that decide when Argon speaks. A readout that
     does not show them describes a different day from the one Argon runs. */
  .blk { display: flex; align-items: center; gap: 10px; padding: 6px 9px;
         border-radius: 9px; color: #C7D4E6; font-size: 12px; }
  .blk.live { background: rgba(101,216,255,0.12); color: #F4F8FF; }
  .blk.gone { color: #6C7A8D; }
  .blk .span { font-size: 10.5px; color: #9BAAC0; flex: none; width: 82px; }
  .blk .what { flex: 1; min-width: 0; overflow: hidden;
               text-overflow: ellipsis; white-space: nowrap; }
  .blk .tick { font-size: 10px; color: #65D8FF; cursor: pointer; flex: none;
               opacity: 0; transition: opacity 120ms ease; }
  .blk:hover .tick { opacity: 1; }
  .blk .mark { font-size: 10px; color: #6C7A8D; flex: none; }

  .hw { display: flex; align-items: center; gap: 10px; padding: 5px 9px;
        color: #C7D4E6; font-size: 12px; }
  .hw .name { flex: 1; min-width: 0; overflow: hidden;
              text-overflow: ellipsis; white-space: nowrap; }
  .hw .course { font-size: 10px; color: #6C7A8D; }
  .hw .when { font-size: 10.5px; color: #9BAAC0; flex: none; }
  .hw.urgent .when { color: #FF9F45; }

  .section { display: flex; align-items: center; gap: 8px; margin: 18px 0 8px; }
  .section .dot { width: 6px; height: 6px; border-radius: 50%; }
  .section .name { font-size: 16px; }
  .section .count { font-size: 11px; font-weight: 600; color: #9BAAC0; }

  /* ArgonTaskRow */
  .task { display: flex; align-items: center; gap: 12px; padding: 11px 13px;
          margin-bottom: 7px; border-radius: 18px;
          background: rgba(12,23,41,0.82);
          border: 1px solid rgba(255,255,255,0.07); }
  .ring { flex: 0 0 auto; width: 24px; height: 24px; border-radius: 50%;
          border: 1.5px solid; display: flex; align-items: center;
          justify-content: center; }
  .ring.started { box-shadow: 0 0 7px rgba(93,169,255,0.55); }
  /* A solid glyph, so it must beat the stroked base rule above — CSS wins over
     SVG presentation attributes, so setting fill inline would not work. */
  .ring svg { width: 8px; height: 8px; fill: #A9DDFF; stroke: none; }
  .task .body { flex: 1; min-width: 0; }
  .task .name { font-size: 14px; overflow: hidden; text-overflow: ellipsis;
                white-space: nowrap; }
  .task .meta { font-size: 10px; font-weight: 500; color: #9BAAC0; margin-top: 3px;
                overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .pill { flex: 0 0 auto; font-size: 8px; font-weight: 700; letter-spacing: 0.9px;
          padding: 4px 7px; border-radius: 99px; }
  .pending { opacity: 0.4; pointer-events: none; }

  /* Interactive affordances. The ring starts a task, the tick completes it —
     the same two gestures the app binds to tap and swipe. */
  .ring, .tick, .btn { cursor: pointer; }
  .ring:hover { background: rgba(169,221,255,0.12); }
  .tick { flex: 0 0 auto; width: 22px; height: 22px; border-radius: 50%;
          display: flex; align-items: center; justify-content: center;
          opacity: 0; transition: opacity 120ms ease; }
  .tick svg { width: 12px; height: 12px; stroke: #7BE3A0; }
  .task:hover .tick { opacity: 1; }
  .tick:hover { background: rgba(123,227,160,0.14); }

  .actions { display: flex; gap: 7px; margin-top: 13px; }
  .btn { flex: 1; display: flex; align-items: center; justify-content: center;
         gap: 6px; padding: 8px 6px; border-radius: 13px; font-size: 10.5px;
         font-weight: 600; color: #A9DDFF; background: rgba(255,255,255,0.045);
         border: 1px solid rgba(255,255,255,0.07); }
  .btn:hover { background: rgba(93,169,255,0.14); border-color: rgba(93,169,255,0.30); }
  .btn svg { width: 11px; height: 11px; }

  /* Argon's open questions: the only thing here waiting on an answer. */
  .q { margin: 4px 0 10px; }
  .qtext { font-size: 12px; color: #F4F8FF; line-height: 1.35; }
  .qacts { display: flex; gap: 6px; flex-wrap: wrap; margin-top: 6px; }
  .qbtn { padding: 5px 10px; border-radius: 8px; cursor: pointer; font-size: 11px;
          border: 1px solid rgba(169,221,255,0.16);
          background: rgba(18,33,58,0.75); color: #A9DDFF;
          transition: background 120ms ease; }
  .qbtn:hover { background: rgba(39,93,255,0.30); }

  .dormant { opacity: 0.55; }
  .empty { text-align: center; padding: 22px 8px; }
  .empty svg { width: 28px; height: 28px; stroke: #A9DDFF; stroke-width: 1.7;
               filter: drop-shadow(0 0 9px rgba(93,169,255,0.46)); }
  .empty .headline { font-size: 19px; margin-top: 10px; }
  .empty .sub { font-size: 11px; color: #9BAAC0; margin-top: 5px; line-height: 1.5; }

  .rule { height: 1px; margin: 16px 0 12px;
          background: linear-gradient(90deg, rgba(255,255,255,0.10), transparent); }
  .kv { display: flex; justify-content: space-between; gap: 12px;
        font-size: 11px; padding: 2px 0; }
  .kv .k { color: #9BAAC0; }
  .kv .v { color: #F4F8FF; text-align: right; }
  .kv .v.warn { color: #FF9F45; }

  .foot { margin-top: 12px; font-size: 9.5px; letter-spacing: 0.5px;
          color: #55606F; text-align: right; }
  .offline { text-align: center; padding: 10px 4px; }
  .offline .orb { margin: 0 auto; }
  .offline .headline { font-size: 19px; margin-top: 11px; }
  .offline .msg { font-size: 11px; color: #9BAAC0; margin-top: 6px;
                  line-height: 1.5; word-break: break-word; }
`;

// Minimal stroked glyphs standing in for the app's SF Symbols. Inline because
// a widget cannot reach the network for an icon font.
const GLYPH = {
  "lock.fill": "M7 9V6.5a3 3 0 016 0V9M5.5 9h9v7.5h-9z",
  sparkles: "M10 3l1.6 4.4L16 9l-4.4 1.6L10 15l-1.6-4.4L4 9l4.4-1.6z",
  "moon.stars.fill": "M14.5 11.5A5.5 5.5 0 018 5a5.5 5.5 0 106.5 6.5z",
  "moon.zzz.fill": "M14.5 11.5A5.5 5.5 0 018 5a5.5 5.5 0 106.5 6.5z",
  "checkmark.seal.fill": "M6.5 10l2.5 2.5 4.5-5",
  checkmark: "M5 10.5l3.2 3.2L15 6.5",
  "plus.circle": "M10 6v8M6 10h8",
  "lock.open": "M6 9.5h8V16H6zM8 9.5V7a2.5 2.5 0 015 0",
  "bolt.slash.fill": "M11 3l-5 7h3l-1 5 5-7h-3zM4 4l12 12",
  "graduationcap.fill": "M3 8l7-3 7 3-7 3zM6 10v3.5c0 1 1.8 1.8 4 1.8s4-.8 4-1.8V10",
  "book.fill": "M4 4.5h5.5v11H4zM10.5 4.5H16v11h-5.5z",
  timer: "M10 5.5v4.5l3 2M10 3.5a6.5 6.5 0 100 13 6.5 6.5 0 000-13z",
  checklist: "M4 6l1.5 1.5L8 5M4 12l1.5 1.5L8 11M11 6.5h5M11 12.5h5",
  questionmark: "M8 7.5a2 2 0 113 1.7c-.6.4-1 .8-1 1.8M10 14.2v.1",
};

const Icon = ({ name, className }) => (
  <svg className={className} viewBox="0 0 20 20">
    <path d={GLYPH[name] || GLYPH.questionmark} />
  </svg>
);

const KV = ({ k, v, warn }) =>
  v ? (
    <div className="kv">
      <span className="k">{k}</span>
      <span className={warn ? "v warn" : "v"}>{v}</span>
    </div>
  ) : null;

export const render = ({ output }) => {
  let v;
  try {
    v = JSON.parse(output);
  } catch (e) {
    // The first tick before any output exists, or a script that died.
    return <div className="offline"><div className="eyebrow">Argon</div>
      <div className="msg">Starting…</div></div>;
  }

  if (!v.ok) {
    return (
      <div className="offline">
        <div className="orb"><Icon name="bolt.slash.fill" /></div>
        <div className="display headline">Can’t reach Argon</div>
        <div className="msg">{v.error}</div>
      </div>
    );
  }

  // Asleep: outside the active hours, or paused by hand. Says so rather than
  // going blank, because a widget that vanishes reads as broken — and it is
  // deliberately not styled as an error, since nothing is wrong.
  if (v.dormant) {
    return (
      <div className="offline dormant">
        <div className="orb"><Icon name="moon.zzz.fill" /></div>
        <div className="display headline">Argon is asleep</div>
        <div className="msg">{v.reason}</div>
      </div>
    );
  }

  const groups = v.groups || [];
  const inbox = v.inbox || [];
  const focus = v.focus || {};
  const phone = v.phone || {};

  return (
    <div>
      <div className="hero">
        <div className="orb"><Icon name={v.hero.icon} /></div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div className="eyebrow">{v.hero.eyebrow}</div>
          <div className="display title">{v.hero.title}</div>
          {v.period && <div className="caption">{v.period}</div>}
        </div>
      </div>

      <div className="metrics">
        {v.metrics.map((m) => (
          <div className="metric" key={m.label}>
            <Icon name={m.icon} />
            <div className="display value">{m.value}</div>
            <div className="label">{m.label}</div>
          </div>
        ))}
      </div>

      {v.alert && (
        <div className="alert">
          <span>▲</span>
          <span>{v.alert}</span>
        </div>
      )}

      {groups.length === 0 && v.notice && (
        <div className="empty">
          <Icon name="checkmark.seal.fill" />
          <div className="display headline">{v.notice.text}</div>
          {v.notice.tone === "calm" && (
            <div className="sub">
              Anything you or Argon adds shows up here on the same shared list.
            </div>
          )}
        </div>
      )}

      {inbox.length > 0 && (
        <div>
          <div className="section">
            <span className="dot" style={{ background: "#65D8FF",
                                           boxShadow: "0 0 5px #65D8FF" }} />
            <span className="display name">Argon asked</span>
            <span className="count">{inbox.length}</span>
          </div>
          {inbox.map((q) => (
            <div key={q.id} className="q">
              <div className="qtext">{q.text}</div>
              <div className="qacts">
                {q.actions.filter((a) => a.task_id).map((a) => (
                  <span key={a.action} className="qbtn"
                        onClick={(e) => act(e, a.action, a.task_id)}>
                    {a.label}
                  </span>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}

      {groups.map((g) => (
        <div key={g.title}>
          <div className="section">
            <span className="dot" style={{ background: g.tint,
                                           boxShadow: "0 0 5px " + g.tint }} />
            <span className="display name">{g.title}</span>
            <span className="count">{g.tasks.length}</span>
          </div>
          {g.tasks.map((t) => (
            <div className="task" key={t.id}>
              <span className={t.started ? "ring started" : "ring"}
                    style={{ borderColor: t.tint }}
                    title={t.started ? "Already running" : "Start this task"}
                    onClick={(e) => !t.started && act(e, "start", t.id)}>
                {t.started && (
                  <svg viewBox="0 0 10 10"><path d="M3 2l5 3-5 3z" /></svg>
                )}
              </span>
              <div className="body">
                <div className="display name">{t.title}</div>
                {t.meta && <div className="meta">{t.meta}</div>}
              </div>
              <span className="pill" style={{ color: t.tint,
                                              background: t.tint + "1A" }}>
                {t.priority.toUpperCase()}
              </span>
              <span className="tick" title="Complete"
                    onClick={(e) => act(e, "complete", t.id, t.title)}>
                <Icon name="checkmark" />
              </span>
            </div>
          ))}
        </div>
      ))}

      <div className="rule" />
      <KV k="Focus" v={focus.label} />
      <KV k="Until" v={focus.until} />
      {focus.mode !== "off" && <KV k="Early exit" v={focus.early_exit} />}
      <KV k="Phone" v={phone.applied} />
      <KV k="Converged" v={phone.convergence} warn={phone.drift} />
      <KV k="Because" v={focus.reason} />
      <KV k="Error" v={phone.error} warn />

      <div className="actions">
        <span className="btn" onClick={(e) => act(e, "add")}>
          <Icon name="plus.circle" />Add task
        </span>
        {/* Always offered, never conditional on a lock being visible: an escape
            hatch you can only reach when the UI agrees you are locked is not one. */}
        <span className="btn" onClick={(e) => act(e, "unlock")}>
          <Icon name="lock.open" />Release
        </span>
      </div>

      <div className="foot">{v.updated}{v.cached ? " · CACHED" : ""}</div>
    </div>
  );
};
