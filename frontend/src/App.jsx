import { useEffect, useMemo, useState } from "react";
import { api, socket } from "./api";
import { useVoice } from "./useVoice";
import CallPanel from "./CallPanel.jsx";
import { Appointments, DoctorBoard, Tasks } from "./Board.jsx";
import DoctorManager from "./DoctorManager.jsx";
import History from "./History.jsx";
import { useRing } from "./useRing.jsx";

export default function App() {
  const [mode, setMode] = useState("patient"); // patient = caller, doctor = doctor's screen
  const [doctorId, setDoctorId] = useState("");
  const [lines, setLines] = useState([]);
  const [online, setOnline] = useState(socket.connected);
  const [data, setData] = useState({ doctors: [], appointments: [], tasks: [], due: [] });
  

  // append = true adds the text to the last line of the same speaker (the agent's reply streams in)
  const addLine = (who, text, append) =>
    setLines((l) => {
      const last = l[l.length - 1];
      if (append && last && last.who === who) return [...l.slice(0, -1), { who, text: last.text + " " + text }];
      return [...l, { who, text }];
    });
  const voice = useVoice(addLine);
  const idle = voice.state === "idle";

  // The server pushes fresh data the moment anything changes. No polling.
  useEffect(() => {
    const up = () => {
      setOnline(true);
      socket.emit("hello");
    };
    const down = () => setOnline(false);
    socket.on("connect", up);
    socket.on("disconnect", down);
    socket.on("state", setData);
    if (socket.connected) up();
    return () => {
      socket.off("connect", up);
      socket.off("disconnect", down);
      socket.off("state", setData);
    };
  }, []);

  useEffect(() => {
    if (!doctorId && data.doctors.length) setDoctorId(String(data.doctors[0].id));
  }, [data.doctors, doctorId]);

  const view = useMemo(() => {
    if (mode !== "doctor") return data;
    const mine = (x) => String(x.doctor_id) === doctorId;
    return { ...data, appointments: data.appointments.filter(mine), tasks: data.tasks.filter(mine), due: data.due.filter(mine) };
  }, [data, mode, doctorId]);

  const firstDue = mode === "doctor" && idle ? view.due[0] : null;
  useRing(firstDue ? firstDue.id : null, "Your assistant is calling", firstDue ? firstDue.title : "");

  function startCall(body) {
    setLines([]);
    voice.start(body);
  }

  return (
    <main>
      <header>
        <div className="brand">
          <span className="logo" aria-hidden="true">
            <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
              <path d="M4 12v0M8 8v8M12 4v16M16 8v8M20 12v0" />
            </svg>
          </span>
          <div>
            <h1>Front desk voice agent</h1>
            <span className={`live ${online ? "on" : "off"}`}>
              <i /> {online ? "Live" : "Offline, reconnecting"}
            </span>
          </div>
        </div>

        <div className="tabs" role="tablist">
          <button role="tab" aria-selected={mode === "patient"} className={mode === "patient" ? "on" : ""} disabled={!idle} onClick={() => setMode("patient")}>
            I am a caller
          </button>
          <button role="tab" aria-selected={mode === "doctor"} className={mode === "doctor" ? "on" : ""} disabled={!idle} onClick={() => setMode("doctor")}>
            I am a doctor
          </button>
          <button role="tab" aria-selected={mode === "manage"} className={mode === "manage" ? "on" : ""} disabled={!idle} onClick={() => setMode("manage")}>
            Doctors
          </button>
          <button role="tab" aria-selected={mode === "history"} className={mode === "history" ? "on" : ""} disabled={!idle} onClick={() => setMode("history")}>
            History
          </button>
          {mode === "doctor" && (
            <select aria-label="Doctor" value={doctorId} disabled={!idle} onChange={(e) => setDoctorId(e.target.value)}>
              {data.doctors.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
            </select>
          )}
        </div>
      </header>

      {firstDue && (
        <div className="incoming" role="alert">
          <span className="ring" aria-hidden="true">
            <svg viewBox="0 0 24 24" width="20" height="20" fill="currentColor"><path d="M6.6 10.8a15 15 0 0 0 6.6 6.6l2.2-2.2a1 1 0 0 1 1-.25 11.4 11.4 0 0 0 3.6.57 1 1 0 0 1 1 1V20a1 1 0 0 1-1 1A17 17 0 0 1 3 4a1 1 0 0 1 1-1h3.5a1 1 0 0 1 1 1c0 1.25.2 2.45.57 3.57a1 1 0 0 1-.25 1z" /></svg>
          </span>
          <div className="grow">
            <b>Your assistant is calling</b>
            <div className="muted">{firstDue.title}. Due {firstDue.due}</div>
          </div>
          <button className="go" onClick={() => startCall({ role: "doctor", doctor_id: doctorId, task_id: firstDue.id })}>Answer</button>
          <button className="ghost" onClick={() => api.dismissReminder(firstDue.id)}>Not now</button>
        </div>
      )}

      {mode === "manage" ? (
        <DoctorManager doctors={data.doctors} />
      ) : mode === "history" ? (
        <History />
      ) : (
      <div className="layout">
        <CallPanel
          voice={voice}
          lines={lines}
          title={mode === "patient" ? "Call the front desk" : "Talk to your assistant"}
          onStart={() => startCall(mode === "patient" ? { role: "patient" } : { role: "doctor", doctor_id: doctorId })}
        />
        <div className="side">
          <DoctorBoard doctors={data.doctors} />
          <Appointments appointments={view.appointments} />
          <Tasks tasks={view.tasks} />
        </div>
      </div>
      )}
    </main>
  );
}