import { useState } from "react";
import { api } from "./api";

const STATE_TEXT = {
  available: "Free now",
  in_room_busy: "With a patient",
  off_hours: "Off hours",
  away: "Away",
};

const STATUS_TEXT = { cancelled: "Cancelled", expired: "Expired", done: "Done" };

const initials = (name) =>
  name.replace(/^Dr\.?\s+/i, "").split(" ").map((w) => w[0]).slice(0, 2).join("").toUpperCase();

function Head({ title, count }) {
  return (
    <div className="card-head">
      <h3>{title}</h3>
      {count > 0 && <span className="count">{count}</span>}
    </div>
  );
}

export function DoctorBoard({ doctors }) {
  return (
    <section>
      <Head title="Doctors" count={doctors.length} />
      {doctors.map((d) => (
        <div key={d.id} className="row">
          <span className="avatar">{initials(d.name)}</span>
          <div className="grow">
            <b>{d.name}</b> <span className="muted">{d.specialty}</span>
            <div className="muted">Room {d.room}, floor {d.floor}</div>
          </div>
          <span className={`badge ${d.state}`}>{STATE_TEXT[d.state]}</span>
          <button className="small" onClick={() => api.setPresence(d.id, d.presence === "in_room" ? "away" : "in_room")}>
            {d.presence === "in_room" ? "Mark away" : "Mark in room"}
          </button>
        </div>
      ))}
    </section>
  );
}

function AppointmentRow({ a }) {
  const [moving, setMoving] = useState(false);
  const [when, setWhen] = useState("");
  const [error, setError] = useState("");

  async function cancel() {
    if (!window.confirm(`Cancel ${a.patient}'s meeting with ${a.doctor}?`)) return;
    const r = await api.cancelAppointment(a.id);
    if (r?.error) setError(r.error);
  }

  async function move(e) {
    e.preventDefault();
    if (!when) return;
    const r = await api.moveAppointment(a.id, when);
    if (r?.error) return setError(r.error);
    setMoving(false);
    setWhen("");
    setError("");
  }

  return (
    <div className="row wrap">
      <span className="when">{a.when}</span>
      <div className="grow">
        <b>{a.patient}</b> with {a.doctor}
        <div className="muted">{a.purpose}. Calendar: {a.calendar}</div>
        {error && <div className="muted bad">{error}</div>}
      </div>
      <button className="small" onClick={() => setMoving(!moving)}>Reschedule</button>
      <button className="small danger" onClick={cancel}>Cancel</button>
      {moving && (
        <form className="movebox" onSubmit={move}>
          <input type="datetime-local" value={when} onChange={(e) => setWhen(e.target.value)} aria-label="New date and time" required />
          <button type="submit" className="small">Move</button>
        </form>
      )}
    </div>
  );
}

export function Appointments({ appointments }) {
  return (
    <section>
      <Head title="Upcoming meetings" count={appointments.length} />
      {appointments.length === 0 && <p className="empty">No meetings yet. Book one by voice.</p>}
      {appointments.map((a) => <AppointmentRow key={a.id} a={a} />)}
    </section>
  );
}

export function Tasks({ tasks }) {
  const pending = tasks.filter((t) => t.status === "pending").length;
  return (
    <section>
      <Head title="To-dos and reminders" count={pending} />
      {tasks.length === 0 && <p className="empty">Tasks appear when a meeting is booked, or say "remind me to..." to the assistant.</p>}
      {tasks.map((t) => (
        <div key={t.id} className={`row ${t.status}`}>
          <div className="grow">
            <b>{t.title}</b>
            <div className="muted">
              {t.doctor}. Due {t.due}. {t.reminders} reminder{t.reminders === 1 ? "" : "s"}
              {t.snoozed_until ? `. Snoozed until ${t.snoozed_until}` : ""}
            </div>
          </div>
          {t.status === "pending" ? (
            <button className="small" onClick={() => api.taskDone(t.id)}>Mark done</button>
          ) : (
            <span className="badge done">{STATUS_TEXT[t.status] || "Done"}</span>
          )}
        </div>
      ))}
    </section>
  );
}