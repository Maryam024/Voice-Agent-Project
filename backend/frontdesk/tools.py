"""Actions the agent can take. The LLM decides WHEN to call them; this code does the work and checks the rules."""
import difflib
import json
import re
from datetime import datetime, timedelta

from django.conf import settings
from django.utils import timezone

from . import availability as av
from . import calendar_sync
from .models import Appointment, Doctor, Task


def _doctor(doctor_id):
    doctor = Doctor.objects.filter(id=doctor_id).first()
    if not doctor:
        raise ValueError("Unknown doctor_id. Call find_doctor first.")
    return doctor


def _due_time(start):
    return max(start - timedelta(minutes=settings.TASK_LEAD_MINUTES), timezone.now())


def _same_person(a, b):
    """Speech-to-text misspells names, so a close match is enough."""
    wa, wb = re.findall(r"\w+", a.lower()), re.findall(r"\w+", b.lower())
    return any(difflib.SequenceMatcher(None, x, y).ratio() >= 0.75 for x in wa for y in wb)


def _ranges(slots):
    """['09:00', '09:30', '10:30'] -> '09:00-10:00, 10:30-11:00' (short enough to say aloud)."""
    step, out = timedelta(minutes=av.SLOT), []
    for s in slots:
        t = datetime.strptime(s, "%H:%M")
        if out and out[-1][1] == t:
            out[-1][1] = t + step
        else:
            out.append([t, t + step])
    return ", ".join(f"{a:%H:%M}-{b:%H:%M}" for a, b in out)


def _move(appt, new_start):
    """Move one meeting to a new time and keep its task and Google event in sync."""
    doctor = appt.doctor
    start_dt = av.parse_local(new_start)
    end_dt = start_dt + timedelta(minutes=av.SLOT)
    ok, why = av.is_free(doctor, start_dt, end_dt, exclude_id=appt.id)
    if not ok:
        return {"error": why, "next_free_slot": av.next_free(doctor)}

    appt.start, appt.end = start_dt, end_dt
    appt.save()
    Task.objects.filter(appointment=appt, status="pending").update(
        due_at=_due_time(start_dt), snoozed_until=None, last_reminded_at=None
    )
    try:
        calendar_sync.move_event(appt)
        calendar = "updated"
    except Exception as e:
        calendar = f"calendar sync failed: {e}"
    return {"moved_to": av.label(start_dt), "patient": appt.patient_name, "doctor": doctor.name,
            "room": doctor.room, "floor": doctor.floor, "calendar": calendar}


def cancel_core(appt):
    """Cancel one meeting, stop its to-do and remove its Google event. Used by voice AND by the screen button."""
    appt.status = "cancelled"
    appt.save()
    Task.objects.filter(appointment=appt, status="pending").update(status="cancelled")
    try:
        calendar_sync.delete_event(appt)
        calendar = "removed"
    except Exception as e:
        calendar = f"calendar sync failed: {e}"
    return {"cancelled": True, "patient": appt.patient_name, "doctor": appt.doctor.name,
            "was": av.label(appt.start), "calendar": calendar}


# ---------- patient tools ----------

def find_doctor(ctx, query=""):
    words = [w for w in re.findall(r"\w+", query.lower()) if w not in ("dr", "doctor")]
    doctors = list(Doctor.objects.all())
    if not words:
        return {"doctors": [_doc_info(d) for d in doctors]}

    def matches(d):
        text = f"{d.name} {d.specialty}".lower()
        if all(w in text for w in words):
            return True
        # speech-to-text often misspells names, so allow close matches
        name_words = d.name.lower().replace(".", "").split()
        return any(difflib.SequenceMatcher(None, w, n).ratio() >= 0.7 for w in words for n in name_words)

    return {"doctors": [_doc_info(d) for d in doctors if matches(d)]}


def _doc_info(d):
    return {"id": d.id, "name": d.name, "specialty": d.specialty, "room": d.room, "floor": d.floor,
            "fee": d.fee or "not set"}


def get_doctor_status(ctx, doctor_id):
    return av.doctor_status(_doctor(doctor_id))


def check_availability(ctx, doctor_id, date):
    doctor = _doctor(doctor_id)
    day = av.parse_local(date).date()
    hours = [f"{s:%H:%M}-{e:%H:%M}" for s, e in av.hours_for(doctor, day)]
    if not hours:
        return {"working_hours": [], "free_slots": [], "note": f"{doctor.name} does not work on {day:%A}."}
    slots = av.free_slots(doctor, day)
    return {
        "working_hours": hours,
        "free_ranges": _ranges(slots) or "none, fully booked",
        "earliest": slots[:3],
        "time_zone": str(av.TZ),
        "note": "Meetings are 30 minutes and must end by the end of a range. Offer at most 3 times.",
    }


def book_appointment(ctx, doctor_id, patient_name, start, purpose, confirmed=False):
    if not confirmed:
        return {"error": "Confirm doctor, date, time, time zone and purpose with the caller first."}
    if not patient_name.strip():
        return {"error": "Ask for the patient's name."}
    doctor = _doctor(doctor_id)
    start_dt = av.parse_local(start)
    end_dt = start_dt + timedelta(minutes=av.SLOT)

    ok, why = av.is_free(doctor, start_dt, end_dt)
    if not ok:
        return {"error": why, "next_free_slot": av.next_free(doctor)}

    same = Appointment.objects.filter(
        doctor=doctor, status="booked", start__gte=timezone.now(), patient_name__iexact=patient_name.strip()
    ).first()
    if same:  # a second booking is almost always a "reschedule" that went wrong
        return {"error": f"{patient_name} already has meeting {same.id} with {doctor.name} on {av.label(same.start)}. "
                         "To change it use reschedule_appointment. Only book again if the caller clearly wants two meetings."}

    appt = Appointment.objects.create(
        doctor=doctor, patient_name=patient_name, start=start_dt, end=end_dt, purpose=purpose
    )
    Task.objects.create(
        doctor=doctor,
        appointment=appt,
        title=f"Prepare for {patient_name} ({purpose})",
        due_at=_due_time(start_dt),
    )
    try:
        appt.calendar_event_id = calendar_sync.create_event(appt)
        appt.save()
        calendar = "local only" if appt.calendar_event_id.startswith("local-") else "Google Calendar"
    except Exception as e:
        calendar = f"calendar sync failed: {e}"

    return {
        "booked": True,
        "appointment_id": appt.id,
        "doctor": doctor.name,
        "when": av.label(start_dt),
        "time_zone": str(av.TZ),
        "room": doctor.room,
        "floor": doctor.floor,
        "calendar": calendar,
    }

def get_clinic_info(ctx):
    return {
        "clinic": settings.CLINIC_NAME,
        "phone": settings.CLINIC_PHONE or "not available",
        "address": settings.CLINIC_ADDRESS or "not available",
    }


def find_my_appointments(ctx, patient_name):
    """Upcoming meetings under this name. Fuzzy, because speech-to-text misspells names."""
    name = patient_name.lower().strip()
    qs = Appointment.objects.filter(status="booked", start__gte=timezone.now()).select_related("doctor")
    found = [a for a in qs if difflib.SequenceMatcher(None, name, a.patient_name.lower()).ratio() >= 0.75]
    return {"appointments": [
        {"appointment_id": a.id, "doctor": a.doctor.name, "when": av.label(a.start), "purpose": a.purpose}
        for a in found
    ]}


def _caller_found(session, appointment_id):
    """True if find_my_appointments already returned this meeting during this call."""
    needle = f'"appointment_id": {int(appointment_id)}'
    return any(m.get("role") == "tool" and needle in (m.get("content") or "") for m in session.messages)


def _appointment(ctx, appointment_id):
    """A doctor can only touch their own meetings. A caller reaches meetings found by name."""
    session = ctx["session"]
    if session.role == "patient" and not _caller_found(session, appointment_id):
        raise ValueError("Call find_my_appointments with the caller's name first.")
    qs = Appointment.objects.filter(id=appointment_id, status="booked").select_related("doctor")
    if session.role == "doctor":
        qs = qs.filter(doctor=session.doctor)
    appt = qs.first()
    if not appt:
        raise ValueError("Appointment not found.")
    return appt


def cancel_appointment(ctx, appointment_id, confirmed=False):
    if not confirmed:
        return {"error": "Say the meeting back and get a clear yes first."}
    return cancel_core(_appointment(ctx, appointment_id))


def reschedule_appointment(ctx, appointment_id, patient_name, new_start, confirmed=False):
    if not confirmed:
        return {"error": "Confirm the new time with the caller first."}
    appt = Appointment.objects.filter(id=appointment_id, status="booked").select_related("doctor").first()
    if not appt or not _same_person(patient_name, appt.patient_name):
        return {"error": "No meeting with that id under that name. Call find_my_appointments first."}
    return _move(appt, new_start)



# ---------- doctor tools ----------

def _my_task(ctx, task_id):
    task = Task.objects.filter(id=task_id, doctor=ctx["session"].doctor).first()
    if not task:
        raise ValueError("Task not found.")
    return task


def complete_task(ctx, task_id):
    task = _my_task(ctx, task_id)
    task.status = "done"
    task.save()
    return {"done": task.title}


def snooze_task(ctx, task_id, minutes):
    task = _my_task(ctx, task_id)
    task.snoozed_until = timezone.now() + timedelta(minutes=int(minutes))
    task.last_reminded_at = None
    task.save()
    return {"snoozed_until": av.label(task.snoozed_until), "task": task.title}


def move_appointment(ctx, appointment_id, new_start, confirmed=False):
    if not confirmed:
        return {"error": "Confirm the new time first."}
    return _move(_appointment(ctx, appointment_id), new_start)


def create_task(ctx, title, due_at=None):
    """A to-do for the doctor. Without a time it is due right away."""
    doctor = ctx["session"].doctor
    title = (title or "").strip()
    if not title:
        return {"error": "Ask the doctor what the task is."}
    due = av.parse_local(due_at) if due_at else timezone.now()
    if due < timezone.now() - timedelta(minutes=1):
        return {"error": "That time is in the past. Ask for a later time."}
    task = Task.objects.create(doctor=doctor, title=title[:200], due_at=due)
    return {"created": True, "task_id": task.id, "title": task.title, "due": av.label(task.due_at)}


def set_reminder(ctx, minutes, title="Reminder"):
    """'Remind me in 20 minutes': a task that rings after that many minutes."""
    minutes = int(minutes)
    if minutes < 1 or minutes > 60 * 24 * 7:
        return {"error": "Ask for a reminder time between 1 minute and 7 days."}
    doctor = ctx["session"].doctor
    due = timezone.now() + timedelta(minutes=minutes)
    task = Task.objects.create(doctor=doctor, title=((title or "Reminder").strip() or "Reminder")[:200], due_at=due)
    return {"reminder_set": True, "task_id": task.id, "title": task.title, "rings_at": av.label(due)}


def end_call(ctx):
    """The person said goodbye or needs nothing more. The browser hangs up after the goodbye is spoken."""
    return {"ended": True}


def emergency_help(ctx):
    """The caller describes a medical emergency: no booking, send them to emergency services."""
    ctx["session"].emergency = True
    return {"emergency": True, "number": settings.EMERGENCY_NUMBER,
            "say": f"Please call {settings.EMERGENCY_NUMBER} now or go to the nearest emergency room."}


# ---------- registry ----------

FUNCTIONS = {f.__name__: f for f in [
    find_doctor, get_doctor_status, check_availability, book_appointment,
    get_clinic_info, find_my_appointments, cancel_appointment, move_appointment,
    reschedule_appointment, complete_task, snooze_task, create_task, set_reminder, emergency_help, end_call,
]}


def _schema(name, description, props, required):
    return {"type": "function", "function": {
        "name": name,
        "description": description,
        "parameters": {"type": "object", "properties": props, "required": required},
    }}


_INT = {"type": "integer"}
_STR = {"type": "string"}
_ISO = {"type": "string", "description": "Local clinic time, ISO format like 2026-10-14T15:00"}
_YES = {"type": "boolean", "description": "true only after the person clearly said yes"}

_availability = _schema(
    "check_availability", "List free 30-minute slots of a doctor on one date.",
    {"doctor_id": _INT, "date": {"type": "string", "description": "YYYY-MM-DD"}}, ["doctor_id", "date"],
)
_move_schema = _schema(
    "move_appointment", "Move a meeting to a new time. Confirm the new time first.",
    {"appointment_id": _INT, "new_start": _ISO, "confirmed": _YES}, ["appointment_id", "new_start", "confirmed"],
)
_cancel = _schema(
    "cancel_appointment", "Cancel a meeting. Only after the person clearly said yes.",
    {"appointment_id": _INT, "confirmed": _YES}, ["appointment_id", "confirmed"],
)

_reschedule = _schema(
    "reschedule_appointment", "Move the caller's meeting to a new time. Confirm the new time first.",
    {"appointment_id": _INT, "patient_name": _STR, "new_start": _ISO, "confirmed": _YES},
    ["appointment_id", "patient_name", "new_start", "confirmed"],
)

_END_CALL = _schema("end_call", "Call this when the person says goodbye or clearly needs nothing more. Then say a short goodbye.", {}, [])

SCHEMAS = {
    "patient": [
        _schema("find_doctor", "Find doctors (with room, floor and fee) by name or specialty. Empty query lists all.",
                {"query": _STR}, []),
        _schema("get_doctor_status", "Where the doctor is now (room, floor) and whether they are free right now.",
                {"doctor_id": _INT}, ["doctor_id"]),
        _availability,
        _schema("book_appointment", "Book a 30 minute meeting. Only after the caller confirmed everything.",
                {"doctor_id": _INT, "patient_name": _STR, "start": _ISO, "purpose": _STR, "confirmed": _YES},
                ["doctor_id", "patient_name", "start", "purpose", "confirmed"]),
        _schema("get_clinic_info", "Clinic phone number and address.", {}, []),
        _schema("find_my_appointments", "Upcoming meetings of a caller, by name.",
                {"patient_name": _STR}, ["patient_name"]),
        _reschedule,
        _cancel,
        _END_CALL,
        _schema("emergency_help", "Call this at once if the caller describes a medical emergency "
                "(chest pain, trouble breathing, heavy bleeding, stroke signs, unconscious, overdose, or an accident).", {}, []),
    ],
    "doctor": [
        _schema("complete_task", "Mark a task as done.", {"task_id": _INT}, ["task_id"]),
        _schema("snooze_task", "Remind again later.", {"task_id": _INT, "minutes": _INT}, ["task_id", "minutes"]),
        _move_schema,
        _cancel,
        _availability,
        _schema("create_task", "Add a to-do for the doctor. due_at is optional; without it the task is due now.",
                {"title": _STR, "due_at": _ISO}, ["title"]),
        _END_CALL,
        _schema("set_reminder", "Remind the doctor after some minutes, like 'remind me in 20 minutes'.",
                {"minutes": _INT, "title": _STR}, ["minutes"]),
    ],
}

ENDS_CALL = ("booked", "cancelled", "moved_to", "emergency", "ended")  # a successful result with one of these ends the call


def run(name, arguments, ctx):
    """Execute one tool call from the LLM. Errors go back to the LLM as text so it can recover."""
    try:
        args = json.loads(arguments) if isinstance(arguments, str) else arguments
        result = FUNCTIONS[name](ctx, **(args or {}))
    except Exception as e:
        return {"error": str(e)}
    if isinstance(result, dict) and "error" not in result and any(k in result for k in ENDS_CALL):
        ctx["session"].end_call = True  # run_turn tells the browser to hang up after the goodbye
    return result