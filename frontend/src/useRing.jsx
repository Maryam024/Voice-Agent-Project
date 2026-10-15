import { useEffect, useRef } from "react";

// Ringtone and browser notification for "Your assistant is calling".
// Browsers only allow sound and notifications after the user has clicked something on the page,
// so the first click anywhere unlocks both.
export function useRing(active, title, body) {
  const audio = useRef(null);
  const notified = useRef("");

  useEffect(() => {
    const unlock = () => {
      try {
        audio.current = audio.current || new (window.AudioContext || window.webkitAudioContext)();
        audio.current.resume();
      } catch {}
      try {
        if ("Notification" in window && Notification.permission === "default") Notification.requestPermission();
      } catch {}
    };
    window.addEventListener("pointerdown", unlock, { once: true });
    return () => window.removeEventListener("pointerdown", unlock);
  }, []);

  // two short beeps, a pause, again, for as long as the call is incoming
  useEffect(() => {
    if (!active) return;
    const beep = (at, freq) => {
      const ctx = audio.current;
      if (!ctx || ctx.state !== "running") return;
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.frequency.value = freq;
      gain.gain.setValueAtTime(0.0001, ctx.currentTime + at);
      gain.gain.exponentialRampToValueAtTime(0.2, ctx.currentTime + at + 0.02);
      gain.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + at + 0.35);
      osc.connect(gain).connect(ctx.destination);
      osc.start(ctx.currentTime + at);
      osc.stop(ctx.currentTime + at + 0.4);
    };
    const ring = () => {
      beep(0, 440);
      beep(0.45, 480);
    };
    ring();
    const timer = setInterval(ring, 2500);
    return () => clearInterval(timer);
  }, [active]);

  // one notification per incoming call (useful when the tab is in the background)
  useEffect(() => {
    if (!active) {
      notified.current = "";
      return;
    }
    if (notified.current === active) return;
    notified.current = active;
    try {
      if ("Notification" in window && Notification.permission === "granted") {
        new Notification(title, { body, tag: "assistant-call", requireInteraction: true });
      }
    } catch {}
  }, [active, title, body]);
}