// Argon "Now" widget for Übersicht — what you are working on, and what's next.
//
// Companion to argon.jsx, not a replacement: that one is the whole dashboard,
// this is the one thing you look at while working. Both run the same
// argon-widget.py --json and render the same `now` object it builds, so they
// cannot disagree about what is running — the server's session is the only
// thing entitled to answer that.
//
// Clicking a task here calls the same --do start the SwiftBar menu calls, which
// PATCHes /v1/tasks/<id> and routes through Argon's own StartTaskTool. So it
// lands everywhere at once: mode goes to "working", the daily log records it,
// the check-in gate stops treating the afternoon as free time, and the iOS app
// and the other widget both show it on their next poll.

import { run } from "uebersicht";

const SCRIPT = "$HOME/.config/argon/argon-widget.py";

export const command = SCRIPT + " --json";
export const refreshFrequency = 30000;

const sh = (s) => "'" + String(s).replace(/'/g, "'\\''") + "'";

// Dim on click; the next poll replaces the DOM with the truth, which is exactly
// when the dimming should stop, so nothing has to undo it.
const act = (event, ...args) => {
  const row = event.currentTarget.closest(".row, .panel");
  if (row) row.classList.add("pending");
  run(SCRIPT + " --do " + args.map(sh).join(" "));
};

export const className = `
  top: 24px; left: 24px;
  width: 300px;
  font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", sans-serif;
  font-size: 12px;
  color: #F4F8FF;
  -webkit-font-smoothing: antialiased;

  background:
    radial-gradient(120% 90% at 100% 0%, rgba(93,169,255,0.20), transparent 60%),
    radial-gradient(120% 90% at 0% 100%, rgba(39,93,255,0.16), transparent 62%),
    linear-gradient(160deg, #081326 0%, #040812 100%);
  border: 1px solid rgba(169,221,255,0.14);
  border-radius: 20px;
  box-shadow: 0 18px 44px rgba(0,0,0,0.46);
  padding: 16px 16px 14px;
  overflow: hidden;

  * { box-sizing: border-box; }

  /* Every icon is inline SVG; without a base rule an unstyled one renders as a
     large black triangle, which is how this looked the first time. */
  svg { fill: none; stroke: currentColor; stroke-width: 1.6;
        stroke-linecap: round; stroke-linejoin: round;
        width: 14px; height: 14px; flex: none; }

  .eyebrow { font-size: 9.5px; letter-spacing: 0.14em; text-transform: uppercase;
             color: #9BAAC0; margin-bottom: 8px; }

  .display { font-family: "Iowan Old Style", Georgia, serif; }

  .live { display: flex; align-items: center; gap: 10px; }
  .pulse { width: 8px; height: 8px; border-radius: 50%; background: #65D8FF;
           box-shadow: 0 0 0 0 rgba(101,216,255,0.7);
           animation: ping 2.4s ease-out infinite; flex: none; }
  @keyframes ping {
    0%   { box-shadow: 0 0 0 0 rgba(101,216,255,0.55); }
    70%  { box-shadow: 0 0 0 9px rgba(101,216,255,0); }
    100% { box-shadow: 0 0 0 0 rgba(101,216,255,0); }
  }

  .title { font-size: 17px; line-height: 1.25; color: #F4F8FF; }
  .sub { font-size: 11px; color: #9BAAC0; margin-top: 4px; }
  .sub.over { color: #FF9F45; }

  .actions { display: flex; gap: 8px; margin-top: 12px; }
  .btn { flex: 1; display: flex; align-items: center; justify-content: center;
         gap: 6px; padding: 7px 8px; border-radius: 10px; cursor: pointer;
         font-size: 11px; border: 1px solid rgba(169,221,255,0.16);
         background: rgba(18,33,58,0.75); transition: background 120ms ease; }
  .btn:hover { background: rgba(39,93,255,0.30); }
  .btn.done { color: #65D8FF; }

  .row { display: flex; align-items: center; gap: 9px; padding: 7px 8px;
         border-radius: 9px; cursor: pointer; transition: background 120ms ease; }
  .row:hover { background: rgba(39,93,255,0.20); }
  .dot { width: 6px; height: 6px; border-radius: 50%; flex: none; }
  .row .name { flex: 1; min-width: 0; overflow: hidden;
               text-overflow: ellipsis; white-space: nowrap; }
  .row .when { font-size: 10px; color: #9BAAC0; flex: none; }

  /* Argon's open questions sit above everything: it is the only thing on the
     desktop waiting on an answer rather than informing. */
  .asked { margin-bottom: 14px; padding-bottom: 12px;
           border-bottom: 1px solid rgba(169,221,255,0.10); }
  .q { margin-top: 6px; }
  .qtext { font-size: 12px; color: #F4F8FF; line-height: 1.35; }
  .qacts { display: flex; gap: 6px; flex-wrap: wrap; margin-top: 6px; }
  .qbtn { padding: 5px 10px; border-radius: 8px; cursor: pointer; font-size: 11px;
          border: 1px solid rgba(169,221,255,0.16);
          background: rgba(18,33,58,0.75); color: #A9DDFF;
          transition: background 120ms ease; }
  .qbtn:hover { background: rgba(39,93,255,0.30); }

  .plan { margin-top: 14px; padding-top: 12px;
          border-top: 1px solid rgba(169,221,255,0.10); }
  .blk { display: flex; align-items: center; gap: 9px; padding: 5px 8px;
         border-radius: 8px; color: #C7D4E6; }
  .blk.live { background: rgba(101,216,255,0.12); color: #F4F8FF; }
  .blk.gone { color: #6C7A8D; }
  .blk .span { font-size: 10px; flex: none; width: 74px; color: #9BAAC0; }
  .blk .what { flex: 1; min-width: 0; overflow: hidden;
               text-overflow: ellipsis; white-space: nowrap; }
  .blk .tick { font-size: 10px; color: #65D8FF; cursor: pointer; flex: none;
               opacity: 0; transition: opacity 120ms ease; }
  .blk:hover .tick { opacity: 1; }

  .due { margin-top: 14px; padding-top: 12px;
         border-top: 1px solid rgba(169,221,255,0.10); }
  .hw { display: flex; align-items: center; gap: 9px; padding: 4px 8px;
        color: #C7D4E6; }
  .hw .name { flex: 1; min-width: 0; overflow: hidden;
              text-overflow: ellipsis; white-space: nowrap; }
  .hw .when { font-size: 10px; color: #9BAAC0; flex: none; }
  .hw.urgent .when { color: #FF9F45; }

  .agenda { margin-top: 14px; padding-top: 12px;
            border-top: 1px solid rgba(169,221,255,0.10); }
  .ev { display: flex; align-items: center; gap: 9px; padding: 4px 8px;
        color: #C7D4E6; }
  .ev .name { flex: 1; min-width: 0; overflow: hidden;
              text-overflow: ellipsis; white-space: nowrap; }
  .ev .when { font-size: 10px; color: #65D8FF; flex: none; }

  .dormant { opacity: 0.55; }
  .empty { color: #9BAAC0; padding: 6px 2px 2px; }
  .err { color: #FF6B6B; }
  .pending { opacity: 0.4; }
  .foot { margin-top: 12px; font-size: 9.5px; color: #55647A;
          display: flex; justify-content: space-between; }
`;

const Icon = ({ name }) => (
  <svg viewBox="0 0 20 20">
    {name === "check" && <polyline points="4,10 8,14 16,6" />}
    {name === "pause" && <g><line x1="7" y1="5" x2="7" y2="15" />
                            <line x1="13" y1="5" x2="13" y2="15" /></g>}
    {name === "play" && <polygon points="6,4 16,10 6,16" />}
    {name === "cal" && <g><rect x="3" y="4" width="14" height="13" rx="2" />
                          <line x1="3" y1="8" x2="17" y2="8" /></g>}
    {name === "cap" && <g><polygon points="10,4 18,8 10,12 2,8" />
                          <path d="M5 9.5V14c0 1 2.2 2 5 2s5-1 5-2V9.5" /></g>}
  </svg>
);

export const render = ({ output }) => {
  if (!output) return <div className="empty">Starting…</div>;

  let v;
  try {
    v = JSON.parse(output);
  } catch (e) {
    // The script prints diagnostics on stdout when it cannot reach the server;
    // showing them beats a blank panel that looks like "nothing to do".
    return <div className="err">{String(output).slice(0, 180)}</div>;
  }

  if (!v.ok) return <div className="err">{v.error}</div>;

  // Asleep: outside the active hours, or paused by hand. Says so rather than
  // going blank, because a widget that vanishes reads as broken.
  if (v.dormant) {
    return (
      <div className="dormant">
        <div className="eyebrow">Argon</div>
        <div className="empty">{v.reason}</div>
      </div>
    );
  }

  const now = v.now || {};
  const agenda = v.agenda || [];
  const inbox = v.inbox || [];

  return (
    <div>
      {inbox.length > 0 && (
        <div className="asked">
          <div className="eyebrow">Argon asked</div>
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

      {now.state === "running" ? (
        <div className="panel">
          <div className="eyebrow">Working on</div>
          <div className="live">
            <div className="pulse" />
            <div className="display title">{now.title}</div>
          </div>
          <div className={now.over ? "sub over" : "sub"}>
            {[now.elapsed, now.subject, now.estimate].filter(Boolean).join(" · ")}
            {now.over && " · over estimate"}
          </div>
          <div className="actions">
            <div className="btn done" onClick={(e) => act(e, "complete", now.id, now.title)}>
              <Icon name="check" /> Done
            </div>
            <div className="btn" onClick={(e) => act(e, "stop", now.id)}>
              <Icon name="pause" /> Put down
            </div>
          </div>
        </div>
      ) : (
        <div className="panel">
          <div className="eyebrow">Start working on</div>
          {now.picker && now.picker.length ? (
            now.picker.map((t) => (
              <div key={t.id} className="row" onClick={(e) => act(e, "start", t.id)}>
                <span className="dot" style={{ background: t.tint }} />
                <span className="name">{t.title}</span>
                <span className="when">{t.meta}</span>
              </div>
            ))
          ) : (
            <div className="empty">Nothing on the list.</div>
          )}
        </div>
      )}

      {agenda.length > 0 && (
        <div className="agenda">
          <div className="eyebrow">Coming up</div>
          {agenda.map((e) => (
            <div key={e.id} className="ev">
              <Icon name="cal" />
              <span className="name">{e.summary}</span>
              <span className="when">{e.when}</span>
            </div>
          ))}
        </div>
      )}

      <div className="foot">
        <span>{v.updated}</span>
        <span>{v.cached ? "cached" : "live"}</span>
      </div>
    </div>
  );
};
