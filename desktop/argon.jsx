// Argon desktop widget for Übersicht <https://tracesof.net/uebersicht/>.
//
// The shell command is the same script SwiftBar runs, in --json mode, so the
// menu bar and the desktop cannot disagree. All formatting decisions that need
// a clock (countdowns, "overdue 2d") already happened in Python; this file only
// lays out what it is handed.

export const command = "$HOME/.config/argon/argon-widget.py --json";

// Übersicht polls locally and the server caches the Google round-trip, so a
// short interval here costs one LAN request — see TASKS_TTL_S in argon/api/server.py.
export const refreshFrequency = 5000;

export const className = `
  top: 20px; right: 20px;
  width: 320px;
  font-family: -apple-system, BlinkMacSystemFont, sans-serif;
  color: #e8e8ed;
  background: rgba(22, 22, 26, 0.82);
  -webkit-backdrop-filter: blur(24px);
  border: 1px solid rgba(255, 255, 255, 0.09);
  border-radius: 14px;
  padding: 14px 16px;
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.4);
  font-size: 12px;
  line-height: 1.45;

  h1 { font-size: 15px; font-weight: 600; margin: 0 0 2px; letter-spacing: -0.01em; }
  h2 { font-size: 10px; font-weight: 600; text-transform: uppercase;
       letter-spacing: 0.07em; color: #7e7e8a; margin: 14px 0 5px; }
  .row { display: flex; justify-content: space-between; gap: 10px; }
  .row span:last-child { color: #a8a8b4; text-align: right; }
  .warn { color: #ff9f45; }
  .bad { color: #ff6b6b; }
  .dim { color: #7e7e8a; }
  .task { display: flex; gap: 7px; padding: 3px 0;
          border-top: 1px solid rgba(255,255,255,0.05); }
  .task:first-of-type { border-top: none; }
  .task .body { flex: 1; min-width: 0; }
  .task .title { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .task .meta { font-size: 10px; color: #7e7e8a; }
  .foot { margin-top: 12px; font-size: 10px; color: #5e5e68; text-align: right; }
`;

const MODE_ICON = { off: "○", school: "🎓", homework: "📓", lock_in: "🔒", sleep: "🌙" };
const PRIORITY = { high: "🔴", medium: "🟡", low: "⚪️" };
const BAD = ["diverged", "failed"];

const Row = ({ label, value, cls }) => (
  <div className="row"><span>{label}</span><span className={cls}>{value}</span></div>
);

export const render = ({ output }) => {
  let d;
  try {
    d = JSON.parse(output);
  } catch (e) {
    // Covers the first tick before any output exists, and a script that died.
    return <div className="dim">Argon: starting…</div>;
  }

  if (d.error) {
    return (
      <div>
        <h1 className="warn">Argon unreachable</h1>
        <div className="dim">{d.error}</div>
      </div>
    );
  }

  // No optional chaining anywhere in this file: Übersicht transpiles it in its
  // own bundler, and a syntax it rejects takes the whole widget out.
  const ios = d.ios || {};
  const desired = ios.desired || {};
  const actual = ios.actual || {};
  const conv = ios.convergence || {};
  const period = d.school_period || {};
  const tasks = d.tasks || [];
  const off = desired.mode === "off";
  const drift = BAD.includes(conv.state);

  return (
    <div>
      <h1>
        {MODE_ICON[desired.mode] || "?"} {desired.mode}
        {drift && <span className="warn"> ⚠︎</span>}
      </h1>
      {desired.reason && <div className="dim">{desired.reason}</div>}

      <h2>Focus</h2>
      <Row label="Version" value={"v" + desired.version} />
      {desired.until && <Row label="Until" value={desired.until} />}
      {!off && <Row label="Early exit" value={desired.allow_early_end ? "allowed" : "blocked"} />}
      <Row label="Phone" value={`${actual.mode} v${actual.version}${actual.shielded ? " · shielded" : ""}`} />
      <Row label="Converged" value={conv.state} cls={drift ? "warn" : null} />
      {actual.error && <Row label="Error" value={actual.error} cls="bad" />}

      <h2>Session</h2>
      <Row label="State" value={d.mode || "idle"} />
      {d.current_task && <Row label="Doing" value={d.current_task} />}
      {d.work_session_minutes ? <Row label="Working" value={d.work_session_minutes + "m"} /> : null}
      {d.lock_in_minutes ? <Row label="Locked in" value={d.lock_in_minutes + "m"} /> : null}
      {period.status === "in_period" && (
        <Row label={period.period}
             value={`ends ${period.ends_at} · ${period.minutes_remaining}m`} />
      )}

      <h2>Checklist · {tasks.length}</h2>
      {d.tasks_error && <div className="warn">{d.tasks_error}</div>}
      {tasks.length === 0 && !d.tasks_error && <div className="dim">Nothing pending</div>}
      {tasks.map((t) => (
        <div className="task" key={t.id}>
          <span>{PRIORITY[t.priority] || "⚪️"}</span>
          <div className="body">
            <div className={"title" + (t.overdue ? " bad" : "")}>{t.title}</div>
            {t.meta && <div className="meta">{t.meta}</div>}
          </div>
        </div>
      ))}

      <div className="foot">
        {(d.fetched_at || "").slice(11, 19)}{d.tasks_cached ? " · cached" : ""}
      </div>
    </div>
  );
};
