import { useEffect, useRef, useState } from "react";
import { MicVAD } from "@ricky0123/vad-web";
import { socket } from "./api";

const BARGE_IN_MS = 250; // caller must keep talking this long to interrupt the agent

// Live captions while the caller talks. The browser's own speech recognition (Chrome / Edge) writes the words as they
// are spoken. It is only a preview: the real text still comes from the server's Whisper and replaces it.
// Other browsers show "..." while the caller talks. Set VITE_LIVE_LANG=ur-PK in a .env file in /frontend for Urdu.
const SpeechRec = typeof window !== "undefined" ? window.SpeechRecognition || window.webkitSpeechRecognition : null;
const LIVE_LANG = import.meta.env.VITE_LIVE_LANG || "en-US";

// 16-bit mono WAV from the VAD's 16 kHz samples
function toWav(samples) {
  const buf = new ArrayBuffer(44 + samples.length * 2);
  const v = new DataView(buf);
  const text = (at, s) => [...s].forEach((ch, i) => v.setUint8(at + i, ch.charCodeAt(0)));
  text(0, "RIFF");
  v.setUint32(4, 36 + samples.length * 2, true);
  text(8, "WAVEfmt ");
  v.setUint32(16, 16, true);
  v.setUint16(20, 1, true); // PCM
  v.setUint16(22, 1, true); // mono
  v.setUint32(24, 16000, true);
  v.setUint32(28, 32000, true);
  v.setUint16(32, 2, true);
  v.setUint16(34, 16, true);
  text(36, "data");
  v.setUint32(40, samples.length * 2, true);
  samples.forEach((s, i) => v.setInt16(44 + i * 2, Math.max(-1, Math.min(1, s)) * 0x7fff, true));
  return buf;
}

function micMessage(e) {
  if (e?.name === "NotFoundError") return "No microphone found. Plug one in or enable it in Windows Settings > Sound > Input, then restart the call. You can type your message below meanwhile.";
  if (e?.name === "NotAllowedError") return "Microphone permission was denied. Allow it in the browser address bar, then restart the call. You can type below meanwhile.";
  return "The microphone could not start (" + (e?.message || e) + "). You can type your message below.";
}

// States: idle -> listening -> thinking -> speaking -> listening ...
// A voice detector (Silero VAD) runs on the mic all the time: it finds where the caller starts and stops talking.
// The agent's reply arrives sentence by sentence over the socket and is spoken straight away.
export function useVoice(addLine) {
  const [state, setState] = useState("idle");
  const [timings, setTimings] = useState(null);
  const [live, setLive] = useState(""); // what the caller is saying right now (preview)
  const [hearing, setHearing] = useState(false); // the voice detector hears the caller
  const orbRef = useRef(null); // the orb ring is moved directly, so the page does not re-render all the time
  const R = useRef({ state: "idle", queue: [], playing: false, token: 0, seq: 0, turnId: null, srAll: [], srBase: 0, live: false }); // mutable call data

  const go = (s) => {
    R.current.state = s;
    setState(s);
  };

  // ----- speaking: a queue of sentences, played one after the other -----

  function stopPlayback() {
    const c = R.current;
    c.token++; // late "ended" events of the old sentence are ignored
    c.stopNow?.();
    c.stopNow = null;
    c.queue = [];
    c.playing = false;
  }

  function playNext() {
    const c = R.current;
    const item = c.queue.shift();
    if (!item) {
      c.playing = false;
      return maybeFinish();
    }
    c.playing = true;
    const token = ++c.token;
    let timer;
    const done = () => {
      clearTimeout(timer);
      if (c.token === token) playNext();
    };
    if (item.audio) {
      const audio = new Audio("data:audio/wav;base64," + item.audio);
      audio.onended = done;
      c.stopNow = () => audio.pause();
      audio.play().catch(done);
    } else {
      const u = new SpeechSynthesisUtterance(item.text); // browser voice
      c.utterance = u; // keep a reference: Chrome can drop it and then onend never fires
      timer = setTimeout(done, 3000 + item.text.length * 100); // safety net if the browser never says it finished
      u.onend = done;
      u.onerror = done;
      c.stopNow = () => {
        clearTimeout(timer);
        u.onend = u.onerror = null;
        speechSynthesis.cancel();
      };
      speechSynthesis.speak(u);
    }
  }

  function enqueue(item) {
    const c = R.current;
    c.queue.push(item);
    if (c.state !== "speaking") go("speaking");
    if (!c.playing) playNext();
  }

  // Back to listening once the agent is done talking AND the server has sent everything.
  function maybeFinish() {
    const c = R.current;
    if (c.turnId === null || !c.turnDone || c.playing || c.queue.length || c.state === "idle") return;
    if (c.endAfter) {
      c.endAfter = false;
      c.turnId = null;
      addLine("note", "Call ended. Tap the circle to call again.");
      return end();
    }
    c.turnId = null;
    go("listening");
  }

  // ----- live captions of the caller -----

  function showLive() {
    const c = R.current;
    if (!c.live) return;
    const text = c.srAll.slice(c.srBase).join(" ").trim();
    setLive(text || "...");
  }

  function clearLive() {
    R.current.live = false;
    setLive("");
  }

  function startRecognition() {
    const c = R.current;
    if (!SpeechRec || c.state === "idle" || c.rec || c.srOff) return;
    const rec = new SpeechRec();
    rec.continuous = true;
    rec.interimResults = true;
    rec.lang = LIVE_LANG;
    c.rec = rec;
    c.srAll = [];
    c.srBase = 0;
    rec.onresult = (e) => {
      if (c.rec !== rec) return;
      c.srAll = Array.from(e.results, (r) => r[0].transcript);
      showLive();
    };
    rec.onerror = (e) => {
      if (e.error === "not-allowed" || e.error === "service-not-allowed") c.srOff = true; // blocked: show "..." only
    };
    rec.onend = () => {
      // Chrome stops the recognizer after a while: start it again for as long as the call lasts
      if (c.rec === rec) c.rec = null;
      if (c.state !== "idle") setTimeout(startRecognition, 200);
    };
    try {
      rec.start();
    } catch {
      c.rec = null;
    }
  }

  function stopRecognition() {
    const c = R.current;
    const rec = c.rec;
    c.rec = null; // onend then sees it is not the current recognizer and does not restart
    try {
      rec?.abort();
    } catch {}
    c.srAll = [];
    c.srBase = 0;
  }

  // ----- talking to the server -----

  function send(payload) {
    const c = R.current;
    if (c.turnId !== null) socket.emit("interrupt"); // an older turn may still be running on the server
    stopPlayback();
    go("thinking");
    const id = (c.turnId = ++c.seq);
    c.turnDone = false;
    c.endAfter = false;
    c.open = false;
    socket.emit("turn", { id, ...payload });
  }

  // The caller talks over the agent: stop the voice here AND stop the model on the server.
  function interrupt() {
    const c = R.current;
    stopPlayback();
    socket.emit("interrupt");
    c.turnId = null; // the rest of the old answer is ignored
    go("listening");
    c.live = true; // the caller is talking over the agent: show what they say
    showLive();
  }

  useEffect(() => {
    const c = R.current;
    const mine = (m) => c.turnId !== null && m.id === c.turnId;
    const on = {
      user_text: (m) => {
        if (!mine(m)) return;
        clearLive(); // the real text replaces the live preview
        addLine("user", m.text);
      },
      agent_chunk: (m) => {
        if (!mine(m)) return;
        addLine("agent", m.text, c.open); // every sentence is added to the same caption
        c.open = true;
        enqueue(m);
      },
      turn_done: (m) => {
        if (!mine(m)) return;
        clearLive();
        if (m.timings) setTimings(m.timings);
        c.endAfter = !!m.end_call; // the goodbye finishes playing first, then the call ends
        c.turnDone = true;
        maybeFinish();
      },
      turn_error: (m) => {
        if (!mine(m)) return;
        clearLive();
        addLine("error", m.error);
        c.turnDone = true;
        maybeFinish();
      },
      disconnect: () => {
        if (c.state !== "idle") {
          addLine("error", "Connection lost. The call was ended.");
          end();
        }
      },
    };
    Object.entries(on).forEach(([name, fn]) => socket.on(name, fn));
    return () => Object.entries(on).forEach(([name, fn]) => socket.off(name, fn));
  }, []);

  async function start(body) {
    const c = R.current;
    if (!socket.connected) return addLine("error", "Not connected to the server yet. Is the backend running?");
    go("thinking");
    try {
      c.vad = await MicVAD.new({
        startOnLoad: false,
        model: "v5",
        baseAssetPath: "/",
        onnxWASMBasePath: "https://cdn.jsdelivr.net/npm/onnxruntime-web@1.22.0/dist/",
        redemptionFrames: 20, // about 0.6 s of silence ends the caller's turn (a frame is about 32 ms)
        preSpeechPadFrames: 10, // keep the first words, they come before the detector is sure
        minSpeechFrames: 5, // ignore clicks and coughs
        onFrameProcessed: (p) => orbRef.current?.style.setProperty("--lvl", p.isSpeech),
        onSpeechStart: () => {
          c.inSpeech = true;
          setHearing(true);
          c.srBase = c.srAll.length; // words from before this moment do not belong to this sentence
          c.live = c.state === "listening";
          if (c.live) showLive();
          if (c.state === "speaking" || c.state === "thinking") {
            c.bargeTimer = setTimeout(() => c.inSpeech && interrupt(), BARGE_IN_MS);
          }
        },
        onVADMisfire: () => {
          c.inSpeech = false;
          setHearing(false);
          clearLive();
          clearTimeout(c.bargeTimer);
        },
        onSpeechEnd: (audio) => {
          c.inSpeech = false;
          setHearing(false);
          clearTimeout(c.bargeTimer);
          if (c.state === "listening") send({ audio: toWav(audio) });
        },
      });
    } catch (e) {
      go("idle");
      return addLine("error", "The microphone is blocked or the voice detector could not load. Allow the mic and check your internet.");
    }
    try {
      const d = await socket.emitWithAck("start", body);
      if (d.error) throw new Error(d.error);
      c.turnId = ++c.seq;
      c.turnDone = true; // the greeting is one piece
      c.open = true;
      addLine("agent", d.text);
      enqueue({ text: d.text, audio: d.audio });
      try {
        await c.vad.start(); // asks for the microphone; must be awaited or errors escape the try/catch
        c.srOff = false;
        startRecognition();
      } catch (e) {
        addLine("note", micMessage(e));
      }
    } catch (e) {
      addLine("error", e.message);
      end();
    }
  }

  function end() {
    const c = R.current;
    const wasActive = c.state !== "idle";
    stopPlayback();
    if (c.turnId !== null) socket.emit("interrupt");
    if (wasActive && socket.connected) socket.emit("hangup"); // the server writes the call summary
    stopRecognition();
    clearLive();
    setHearing(false);
    c.turnId = null;
    clearTimeout(c.bargeTimer);
    c.vad?.destroy();
    c.vad = null;
    go("idle");
    orbRef.current?.style.setProperty("--lvl", 0);
  }

  // Typing instead of talking (useful in a noisy room or without a mic)
  function sendText(text) {
    const s = R.current.state;
    if (s !== "idle" && s !== "thinking") send({ text });
  }

  return { state, timings, live, hearing, orbRef, start, end, sendText };
}