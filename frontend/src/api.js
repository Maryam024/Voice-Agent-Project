import { io } from "socket.io-client";

// Same site as the page by default. Only set VITE_BACKEND_URL if the frontend is hosted
// separately (Vercel/Netlify), e.g. VITE_BACKEND_URL=https://my-backend.example.com
const BACKEND = import.meta.env.VITE_BACKEND_URL || undefined;

// One live connection for everything: calls, screen data and button clicks.
export const socket = io(BACKEND, { transports: ["websocket"] });

export const api = {
  setPresence: (doctor_id, presence) => socket.emit("presence", { doctor_id, presence }),
  taskDone: (task_id) => socket.emit("task_done", { task_id }),
  dismissReminder: (task_id) => socket.emit("dismiss", { task_id }),
  cancelAppointment: (id) => socket.emitWithAck("appointment_cancel", { id }),
  moveAppointment: (id, start) => socket.emitWithAck("appointment_move", { id, start }),
  loadCalls: () => socket.emitWithAck("calls"),
  saveDoctor: (doctor) => socket.emitWithAck("doctor_save", doctor),
  deleteDoctor: (id) => socket.emitWithAck("doctor_delete", { id }),
};