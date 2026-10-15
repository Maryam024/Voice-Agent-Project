import { useEffect, useRef, useState } from "react";

const STATUS = {
  idle: "Tap the circle to start the call",
  listening: "Listening",
  thinking: "Thinking",
  speaking: "Speaking. Talk to interrupt",
};

const WHO = { agent: "Agent", user: "You", error: "Problem", note: "Call" };

export default function CallPanel({ voice, lines, onStart, title }) {
  const [typed, setTyped] = useState("");
  const bottom = useRef(null);
  const active = voice.state !== "idle";
  const last = lines[lines.length - 1];
  const waiting = voice.state === "thinking" && (!last || last.who === "user");
  const t = voice.timings;
  const status = voice.hearing && voice.state === "listening" ? "Hearing you" : STATUS[voice.state];

  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [lines, waiting, voice.live]);

  function submit(e) {
    e.preventDefault();
    if (typed.trim()) voice.sendText(typed.trim());
    setTyped("");
  }

  return (
    <section className="call">
      <div className="card-head">
        <h2>{title}</h2>
        <span className={`pill ${voice.state}`}>{active ? voice.state : "ready"}</span>
      </div>

      <div className="orb-wrap">
        <button
          ref={voice.orbRef}
          className={`orb ${voice.state}`}
          onClick={active ? voice.end : onStart}
          aria-label={active ? "End call" : "Start call"}
        >
          <svg viewBox="0 0 24 24" width="34" height="34" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            {active ? <rect x="6" y="6" width="12" height="12" rx="2" fill="currentColor" /> : <><rect x="9" y="3" width="6" height="11" rx="3" /><path d="M5 11a7 7 0 0 0 14 0M12 18v3" /></>}
          </svg>
          <span>{active ? "End" : "Start"}</span>
        </button>
      </div>
      <p className="status" aria-live="polite">{status}</p>

      <div className="captions" aria-live="polite">
        {lines.length === 0 && <p className="empty">Live captions appear here once the call starts.</p>}
        {lines.map((l, i) => (
          <div key={i} className={`bubble ${l.who}`}>
            <small>{WHO[l.who]}</small>
            <p>{l.text}</p>
          </div>
        ))}
        {voice.live && (
          <div className="bubble user live" aria-label="You are speaking">
            <small>You</small>
            <p>{voice.live}</p>
          </div>
        )}
        {waiting && !voice.live && <div className="bubble agent typing" aria-label="The agent is thinking"><span /><span /><span /></div>}
        <div ref={bottom} />
      </div>

      {active && (
        <form className="typebox" onSubmit={submit}>
          <input value={typed} onChange={(e) => setTyped(e.target.value)} placeholder="Or type your message" />
          <button type="submit">Send</button>
        </form>
      )}

      {t && (
        <ul className="chips" aria-label="Speed of the last turn">
          <li>Heard you <b>{t.stt_ms} ms</b></li>
          {t.first_ms != null && <li>First words <b>{t.first_ms} ms</b></li>}
          <li>Full reply <b>{t.llm_ms} ms</b></li>
        </ul>
      )}
    </section>
  );
}