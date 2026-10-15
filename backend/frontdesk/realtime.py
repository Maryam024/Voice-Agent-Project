"""Live connection (Socket.IO). The browser keeps one socket open to the agent:
- the agent's reply is streamed sentence by sentence, so speech starts before the model is done
- the screen data (doctors, meetings, tasks) is pushed the moment it changes, no polling."""
import asyncio
import json
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, time as dtime

import socketio
from django.db import close_old_connections
from django.utils import timezone

from . import agent, reminders, speech, stt, tools, tts
from . import availability as av
from .views import _appointment_json, _doctor_json, _task_json
from .models import Appointment, CallSession, Doctor, Reminder, Task, WorkingHour

sio = socketio.AsyncServer(async_mode="asgi", cors_allowed_origins="*", max_http_buffer_size=10_000_000)

CALLS = {}      # socket id -> CallSession id (its keys are the connected browsers)
LOCKS = {}      # socket id -> lock, so one call never runs two turns at the same time
CANCEL = {}     # socket id -> Event, set when the caller interrupts the running turn
_last = {"state": ""}
_watcher = []


async def run(fn, *args):
    """Run slow, blocking code (database, speech to text, LLM) off the event loop."""
    def job():
        try:
            return fn(*args)
        finally:
            close_old_connections()
    return await asyncio.to_thread(job)


# ---------- screen data ----------

def snapshot():
    reminders.expire_old_tasks()  # tasks of meetings that already ended stop ringing
    appointments = Appointment.objects.filter(status="booked", start__gte=timezone.now()).select_related("doctor").order_by("start")
    return {
        "doctors": [_doctor_json(d) for d in Doctor.objects.all()],
        "appointments": [{**_appointment_json(a), "doctor_id": a.doctor_id} for a in appointments],
        "tasks": [_task_json(t) for t in reminders.ordered(Task.objects.select_related("doctor"))],
        "due": [_task_json(t) for t in reminders.due_tasks().select_related("doctor")],
    }


async def push(to=None):
    """Send the screen data to everyone, but only if it changed. Or to one browser, always."""
    state = await run(snapshot)
    if to:
        await sio.emit("state", state, to=to)
        return
    text = json.dumps(state, sort_keys=True)
    if text != _last["state"]:
        _last["state"] = text
        await sio.emit("state", state)


async def _watch():
    """Time passes without anyone clicking (a task becomes due), so check every 2 seconds."""
    asyncio.create_task(run(stt.warm))
    while True:
        await asyncio.sleep(2)
        if CALLS:
            try:
                await push()
            except Exception as e:
                print("live update failed:", e)


@sio.event
async def connect(sid, environ):
    CALLS[sid] = None
    if not _watcher:
        _watcher.append(asyncio.create_task(_watch()))


@sio.event
async def disconnect(sid):
    session_id = CALLS.pop(sid, None)
    lock = LOCKS.pop(sid, None)
    stop = CANCEL.pop(sid, None)
    if stop:
        stop.set()  # nobody is listening any more: stop the model
    if session_id:
        asyncio.create_task(_finish_later(session_id, lock))


async def _finish_later(session_id, lock):
    """Wait for a running turn to stop, then store the outcome and the one-line summary of the call."""
    try:
        if lock:
            async with lock:
                pass
        await run(_finish, session_id)
    except Exception as e:
        print("could not finish the call:", e)


def _finish(session_id):
    session = CallSession.objects.filter(id=session_id).first()
    if session:
        agent.finish_call(session)


@sio.on("hangup")
async def hangup(sid):
    """The browser ended the call: write the summary in the background, the screen does not wait for it."""
    session_id = CALLS.get(sid)
    if session_id:
        CALLS[sid] = None
        asyncio.create_task(_finish_later(session_id, LOCKS.get(sid)))


@sio.on("hello")
async def hello(sid):
    await push(to=sid)


# ---------- the call ----------

def _start(data):
    role = data.get("role", "patient")
    doctor = Doctor.objects.filter(id=data.get("doctor_id")).first()
    if role == "doctor" and not doctor:
        return {"error": "Choose a doctor first."}
    task = Task.objects.filter(id=data.get("task_id"), status="pending").first() if data.get("task_id") else None

    text = speech.spoken(agent.greeting(role, doctor, task))
    session = CallSession.objects.create(role=role, doctor=doctor, messages=[{"role": "assistant", "content": text}])
    if task:  # the agent is calling the doctor about this task: log it
        Reminder.objects.create(task=task, answered=True)
        task.last_reminded_at = timezone.now()
        task.save()
    return {"session_id": session.id, "text": text, "audio": tts.to_b64(text)}


@sio.on("start")
async def start(sid, data):
    try:
        result = await run(_start, data or {})
    except Exception as e:
        return {"error": str(e)}
    if "session_id" in result:
        CALLS[sid] = result["session_id"]
        await push()
    return result  # goes back to the browser as the answer to its emit


def _turn(session_id, data, send, cancel):
    """One caller turn. Runs in a worker thread; send() pushes events to the browser.
    The LLM keeps writing while a second thread turns finished sentences into speech."""
    session = CallSession.objects.filter(id=session_id).first()
    if not session:
        return send("turn_error", {"error": "Unknown session."})

    t0 = time.time()
    text = (data.get("text") or "").strip()
    if not text and data.get("audio"):
        text = stt.transcribe(bytes(data["audio"]), hint=agent.stt_hint())
    t1 = time.time()
    if not text:
        return send("turn_done", {})  # heard only noise
    send("user_text", {"text": text})

    first, voice_ms, jobs, info = [], [], [], {}
    pool = ThreadPoolExecutor(max_workers=1)  # one worker keeps the sentences in order

    def speak(sentence):
        if cancel.is_set():
            return
        t = time.time()
        audio = tts.to_b64(sentence)
        voice_ms.append(int((time.time() - t) * 1000))
        if cancel.is_set():
            return
        if not first:
            first.append(time.time())
        send("agent_chunk", {"text": sentence, "audio": audio})

    try:
        agent.run_turn(session, text, lambda s: jobs.append(pool.submit(speak, s)), cancel, info)
        for job in jobs:
            job.result()
    except Exception as e:
        cancel.set()
        return send("turn_error", {"error": f"The assistant could not answer: {e}"})
    finally:
        pool.shutdown(wait=True)
    t2 = time.time()
    timings = {
        "stt_ms": int((t1 - t0) * 1000),
        "first_ms": int((first[0] - t1) * 1000) if first else None,
        "llm_ms": int((t2 - t1) * 1000),
    }
    if not cancel.is_set():
        agent.log_turn(session, timings, info)
    send("turn_done", {
        "end_call": getattr(session, "end_call", False),  # booked / moved / cancelled: browser hangs up after the goodbye
        "timings": timings,
    })


@sio.on("turn")
async def turn(sid, data):
    loop = asyncio.get_running_loop()

    def send(event, payload):  # called from the worker thread; waits, so events keep their order
        payload["id"] = data["id"]
        asyncio.run_coroutine_threadsafe(sio.emit(event, payload, to=sid), loop).result()

    async with LOCKS.setdefault(sid, asyncio.Lock()):
        cancel = CANCEL[sid] = threading.Event()
        try:
            await run(_turn, CALLS.get(sid), data, send, cancel)
        except Exception as e:
            await sio.emit("turn_error", {"id": data.get("id"), "error": str(e)}, to=sid)
        await push()


@sio.on("interrupt")
async def interrupt(sid):
    """The caller started talking over the agent: stop the model and the voice work right away."""
    stop = CANCEL.get(sid)
    if stop:
        stop.set()


# ---------- buttons on the screen ----------

@sio.on("presence")
async def presence(sid, data):
    if data.get("presence") in ("in_room", "away"):
        await run(lambda: Doctor.objects.filter(id=data["doctor_id"]).update(presence=data["presence"]))
        await push()


@sio.on("task_done")
async def task_done(sid, data):
    await run(lambda: Task.objects.filter(id=data["task_id"]).update(status="done"))
    await push()


def _dismiss(task_id):
    """Doctor dismissed the incoming call. We log it and the task comes back after the repeat gap."""
    task = Task.objects.filter(id=task_id).first()
    if task:
        Reminder.objects.create(task=task, answered=False)
        task.last_reminded_at = timezone.now()
        task.save()


@sio.on("dismiss")
async def dismiss(sid, data):
    await run(_dismiss, data["task_id"])
    await push()


# ---------- manual buttons on the meetings list ----------

def _booked(appointment_id):
    return Appointment.objects.filter(id=appointment_id, status="booked").select_related("doctor").first()


def _cancel_appointment(appointment_id):
    appt = _booked(appointment_id)
    if not appt:
        return {"error": "Meeting not found."}
    tools.cancel_core(appt)
    return {"ok": True}


def _move_appointment(appointment_id, new_start):
    appt = _booked(appointment_id)
    if not appt:
        return {"error": "Meeting not found."}
    try:
        result = tools._move(appt, new_start)
    except ValueError:
        return {"error": "Choose a valid date and time."}
    return {"error": result["error"]} if "error" in result else {"ok": True}


@sio.on("appointment_cancel")
async def appointment_cancel(sid, data):
    result = await run(_cancel_appointment, (data or {}).get("id"))
    await push()
    return result


@sio.on("appointment_move")
async def appointment_move(sid, data):
    result = await run(_move_appointment, (data or {}).get("id"), (data or {}).get("start"))
    await push()
    return result


# ---------- call history and metrics ----------

def _calls(limit=60):
    out = []
    for c in CallSession.objects.select_related("doctor").order_by("-created_at")[:limit]:
        lines = agent.transcript(c)
        out.append({
            "id": c.id, "role": c.role, "doctor": c.doctor.name if c.doctor else "",
            "when": av.label(c.created_at),
            "seconds": int((c.ended_at - c.created_at).total_seconds()) if c.ended_at else None,
            "open": c.ended_at is None,
            "summary": c.summary, "outcome": c.outcome,
            "turns": sum(1 for l in lines if l["who"] == "user"),
            "transcript": lines,
        })
    return out


def _avg(values):
    values = [v for v in values if v is not None]
    return int(sum(values) / len(values)) if values else None


def _metrics():
    today = datetime.combine(av.clinic_now().date(), dtime.min, av.TZ)
    todays = CallSession.objects.filter(created_at__gte=today)
    turns = [m for c in CallSession.objects.order_by("-created_at")[:200] for m in (c.metrics or [])]
    models = Counter(m.get("model") or "unknown" for m in turns)
    return {
        "calls_today": todays.count(),
        "bookings_today": todays.filter(outcome="booked").count(),
        "emergencies_today": todays.filter(outcome="emergency").count(),
        "turns": len(turns),
        "avg_stt_ms": _avg([m.get("stt_ms") for m in turns]),
        "avg_first_ms": _avg([m.get("first_ms") for m in turns]),
        "avg_llm_ms": _avg([m.get("llm_ms") for m in turns]),
        "fallback_pct": round(100 * sum(1 for m in turns if m.get("fallback")) / len(turns)) if turns else 0,
        "models": [{"model": k, "turns": v} for k, v in models.most_common()],
    }


@sio.on("calls")
async def calls(sid):
    return {"calls": await run(_calls), "metrics": await run(_metrics)}


# ---------- doctors tab: add / edit / delete ----------

def _save_doctor(data):
    name = (data.get("name") or "").strip()
    room = str(data.get("room") or "").strip()
    if not name or not room:
        return {"error": "Name and room are required."}
    d = Doctor.objects.filter(id=data.get("id")).first() or Doctor()
    d.name, d.room = name, room
    d.specialty = (data.get("specialty") or "").strip()
    d.floor = int(data.get("floor") or 0)
    d.fee = int(data.get("fee") or 0)
    d.calendar_id = (data.get("calendar_id") or "").strip()
    d.save()
    if data.get("days") is not None:  # replace the working hours
        d.hours.all().delete()
        for weekday in data["days"]:
            WorkingHour.objects.create(doctor=d, weekday=int(weekday), start=data["start"], end=data["end"])
    return {"ok": True}


def _delete_doctor(doctor_id):
    d = Doctor.objects.filter(id=doctor_id).first()
    if not d:
        return {"error": "Doctor not found."}
    if Appointment.objects.filter(doctor=d, status="booked", start__gte=timezone.now()).exists():
        return {"error": "This doctor still has upcoming meetings. Cancel or move them first."}
    d.delete()
    return {"ok": True}


@sio.on("doctor_save")
async def doctor_save(sid, data):
    try:
        result = await run(_save_doctor, data or {})
    except Exception as e:
        return {"error": str(e)}
    await push()
    return result  # goes back to the browser as the answer to its emit


@sio.on("doctor_delete")
async def doctor_delete(sid, data):
    result = await run(_delete_doctor, (data or {}).get("id"))
    await push()
    return result