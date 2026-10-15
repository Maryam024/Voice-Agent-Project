import { useState } from "react";
import { api } from "./api";

const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const EMPTY = { name: "", specialty: "", room: "", floor: 1, fee: 0, calendar_id: "", days: [0, 1, 2, 3, 4], start: "09:00", end: "17:00" };

export default function DoctorManager({ doctors }) {
  const [form, setForm] = useState(EMPTY);
  const [msg, setMsg] = useState("");
  const set = (key, value) => setForm((f) => ({ ...f, [key]: value }));
  const toggleDay = (i) => set("days", form.days.includes(i) ? form.days.filter((d) => d !== i) : [...form.days, i]);

  async function save(e) {
    e.preventDefault();
    const r = await api.saveDoctor(form);
    setMsg(r.error || "Saved.");
    if (!r.error) setForm(EMPTY);
  }

  async function remove(d) {
    if (!window.confirm(`Delete ${d.name}?`)) return;
    const r = await api.deleteDoctor(d.id);
    setMsg(r.error || "Deleted.");
  }

  const edit = (d) =>
    setForm({ id: d.id, name: d.name, specialty: d.specialty, room: d.room, floor: d.floor, fee: d.fee,
              calendar_id: d.calendar_id, days: d.days, start: d.start, end: d.end });

  return (
    <div className="layout">
      <section>
        <div className="card-head"><h3>{form.id ? "Edit doctor" : "Add doctor"}</h3></div>
        <form className="form" onSubmit={save}>
          <label>Name<input value={form.name} onChange={(e) => set("name", e.target.value)} placeholder="Dr. Ali Raza" /></label>
          <label>Specialty<input value={form.specialty} onChange={(e) => set("specialty", e.target.value)} /></label>
          <label>Room<input value={form.room} onChange={(e) => set("room", e.target.value)} /></label>
          <label>Floor<input type="number" value={form.floor} onChange={(e) => set("floor", e.target.value)} /></label>
          <label>Fee<input type="number" value={form.fee} onChange={(e) => set("fee", e.target.value)} /></label>
          <label>Google Calendar ID<input value={form.calendar_id} onChange={(e) => set("calendar_id", e.target.value)} /></label>
          <label>From<input type="time" value={form.start} onChange={(e) => set("start", e.target.value)} /></label>
          <label>To<input type="time" value={form.end} onChange={(e) => set("end", e.target.value)} /></label>
          <div className="wide days">
            {DAYS.map((d, i) => (
              <label key={d}><input type="checkbox" checked={form.days.includes(i)} onChange={() => toggleDay(i)} /> {d}</label>
            ))}
          </div>
          <div className="wide">
            <button type="submit">{form.id ? "Save changes" : "Add doctor"}</button>{" "}
            {form.id && <button type="button" className="ghost" onClick={() => setForm(EMPTY)}>Cancel edit</button>}
            {msg && <span className="muted"> {msg}</span>}
          </div>
        </form>
      </section>

      <section>
        <div className="card-head"><h3>Doctors</h3></div>
        {doctors.map((d) => (
          <div key={d.id} className="row">
            <div className="grow">
              <b>{d.name}</b> <span className="muted">{d.specialty}</span>
              <div className="muted">Room {d.room}, floor {d.floor}. Fee {d.fee || "not set"}</div>
            </div>
            <button className="small" onClick={() => edit(d)}>Edit</button>
            <button className="small ghost" onClick={() => remove(d)}>Delete</button>
          </div>
        ))}
      </section>
    </div>
  );
}