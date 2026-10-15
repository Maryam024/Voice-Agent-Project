import { useEffect, useState } from "react";
import { api } from "./api";

const OUTCOME = {
  booked: "Booked",
  moved: "Moved",
  cancelled: "Cancelled",
  emergency: "Emergency",
  task: "To-do",
  info: "Information",
};

const ms = (v) => (v == null ? "-" : `${v} ms`);

function Stat({ label, value }) {
  return (
    <div className="stat">
      <b>{value}</b>
      <span>{label}</span>
    </div>
  );
}

export default function History() {
  const [data, setData] = useState(null);
  const [open, setOpen] = useState(null);

  // loads when the tab opens and refreshes every 5 seconds while it is open
  useEffect(() => {
    let alive = true;
    const load = () => api.loadCalls().then((d) => alive && setData(d)).catch(() => {});
    load();
    const timer = setInterval(load, 5000);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, []);

  if (!data) return <section><p className="empty">Loading...</p></section>;
  const m = data.metrics;

  return (
    <div className="history">
      <section>
        <div className="card-head"><h3>Metrics</h3></div>
        <div className="stats">
          <Stat label="Calls today" value={m.calls_today} />
          <Stat label="Bookings today" value={m.bookings_today} />
          <Stat label="Emergencies today" value={m.emergencies_today} />
          <Stat label="Avg. hearing you" value={ms(m.avg_stt_ms)} />
          <Stat label="Avg. first words" value={ms(m.avg_first_ms)} />
          <Stat label="Avg. full reply" value={ms(m.avg_llm_ms)} />
          <Stat label="Fallback model used" value={`${m.fallback_pct}%`} />
          <Stat label="Turns measured" value={m.turns} />
        </div>
        {m.models.length > 0 && (
          <p className="muted models">
            Models: {m.models.map((x) => `${x.model} (${x.turns})`).join(", ")}
          </p>
        )}
      </section>

      <section>
        <div className="card-head">
          <h3>Call history</h3>
          <span className="count">{data.calls.length}</span>
        </div>
        {data.calls.length === 0 && <p className="empty">No calls yet. Make one and it will appear here.</p>}
        {data.calls.map((c) => (
          <div key={c.id} className="call-row">
            <button className="ghost call-head" onClick={() => setOpen(open === c.id ? null : c.id)} aria-expanded={open === c.id}>
              <span className="when">{c.when}</span>
              <span className="grow">
                <b>{c.role === "doctor" ? `Doctor call${c.doctor ? ` (${c.doctor})` : ""}` : "Caller"}</b>
                <span className="muted summary">
                  {c.summary || (c.open ? "Call in progress or not finished properly." : "No summary.")}
                </span>
              </span>
              {c.outcome && <span className={`badge outcome ${c.outcome}`}>{OUTCOME[c.outcome] || c.outcome}</span>}
              <span className="muted small-text">{c.seconds != null ? `${c.seconds}s` : ""}</span>
            </button>
            {open === c.id && (
              <div className="transcript">
                {c.transcript.map((l, i) => (
                  <div key={i} className={`bubble ${l.who}`}>
                    <small>{l.who === "agent" ? "Agent" : "You"}</small>
                    <p>{l.text}</p>
                  </div>
                ))}
              </div>
            )}
          </div>
        ))}
      </section>
    </div>
  );
}