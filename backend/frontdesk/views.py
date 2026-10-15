import json
import time

from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from . import agent, reminders, tts, stt
from . import availability as av
from .models import Appointment, CallSession, Doctor, Reminder, Task

# CSRF is off for these API views to keep the demo simple. Add real authentication before production.


def _body(request):
    return json.loads(request.body or "{}")


def _error(message, status=400):
    return JsonResponse({"error": message}, status=status)

def _doctor_json(d):
    s = av.doctor_status(d)
    hours = list(d.hours.all())
    return {"id": d.id, "name": d.name, "specialty": d.specialty, "room": d.room, "floor": d.floor,
            "presence": d.presence, "state": s["state"], "message": s["message"],
            "fee": d.fee, "calendar_id": d.calendar_id,
            # the form edits one set of hours for all working days
            "days": sorted(h.weekday for h in hours),
            "start": hours[0].start.strftime("%H:%M") if hours else "09:00",
            "end": hours[0].end.strftime("%H:%M") if hours else "17:00"}


def _task_json(t):
    return {"id": t.id, "title": t.title, "doctor": t.doctor.name, "doctor_id": t.doctor_id,
            "due": av.label(t.due_at), "status": t.status,
            "snoozed_until": av.label(t.snoozed_until) if t.snoozed_until else None,
            "reminders": t.reminder_set.count()}


def _appointment_json(a):
    return {"id": a.id, "doctor": a.doctor.name, "patient": a.patient_name, "when": av.label(a.start),
            "purpose": a.purpose, "calendar": "local" if a.calendar_event_id.startswith("local-") else "google"}


# ---------- voice ----------

@csrf_exempt
@require_POST
def voice_start(request):
    data = _body(request)
    role = data.get("role", "patient")
    doctor = Doctor.objects.filter(id=data.get("doctor_id")).first()
    if role == "doctor" and not doctor:
        return _error("Choose a doctor first.")
    task = Task.objects.filter(id=data.get("task_id"), status="pending").first() if data.get("task_id") else None

    text = agent.greeting(role, doctor, task)
    session = CallSession.objects.create(role=role, doctor=doctor, messages=[{"role": "assistant", "content": text}])
    if task:  # the agent is calling the doctor about this task: log it
        Reminder.objects.create(task=task, answered=True)
        task.last_reminded_at = timezone.now()
        task.save()
    return JsonResponse({"session_id": session.id, "text": text, "audio": tts.to_b64(text)})


@csrf_exempt
@require_POST
def voice_turn(request):
    session = CallSession.objects.filter(id=request.POST.get("session_id")).first()
    if not session:
        return _error("Unknown session.", 404)

    t0 = time.time()
    text = request.POST.get("text", "").strip()
    if not text and "audio" in request.FILES:
        text = stt.transcribe(request.FILES["audio"].read(), hint=agent.stt_hint())
    t1 = time.time()
    if not text:
        return JsonResponse({"user_text": "", "reply": "", "audio": None})

    try:
        reply = agent.run_turn(session, text)
    except Exception as e:
        return _error(f"The assistant could not answer: {e}", 500)
    t2 = time.time()
    audio = tts.to_b64(reply)
    t3 = time.time()

    return JsonResponse({
        "user_text": text, "reply": reply, "audio": audio,
        "timings": {"stt_ms": int((t1 - t0) * 1000), "llm_ms": int((t2 - t1) * 1000), "tts_ms": int((t3 - t2) * 1000)},
    })


@require_GET
def transcript(request, session_id):
    session = CallSession.objects.filter(id=session_id).first()
    return JsonResponse({"transcript": agent.transcript(session) if session else []})


# ---------- data for the screen ----------

@require_GET
def doctors(request):
    return JsonResponse({"doctors": [_doctor_json(d) for d in Doctor.objects.all()]})


@csrf_exempt
@require_POST
def set_presence(request, doctor_id):
    presence = _body(request).get("presence")
    if presence not in ("in_room", "away"):
        return _error("presence must be in_room or away")
    Doctor.objects.filter(id=doctor_id).update(presence=presence)
    return JsonResponse({"ok": True})


@require_GET
def appointments(request):
    qs = Appointment.objects.filter(status="booked", start__gte=timezone.now()).select_related("doctor").order_by("start")
    if request.GET.get("doctor_id"):
        qs = qs.filter(doctor_id=request.GET["doctor_id"])
    return JsonResponse({"appointments": [_appointment_json(a) for a in qs]})


@require_GET
def tasks(request):
    qs = reminders.ordered(Task.objects.select_related("doctor"))
    if request.GET.get("doctor_id"):
        qs = qs.filter(doctor_id=request.GET["doctor_id"])
    return JsonResponse({"tasks": [_task_json(t) for t in qs]})


@csrf_exempt
@require_POST
def task_done(request, task_id):
    Task.objects.filter(id=task_id).update(status="done")
    return JsonResponse({"ok": True})


# ---------- reminders ----------

@require_GET
def due_reminders(request):
    qs = reminders.due_tasks(request.GET.get("doctor_id")).select_related("doctor")
    return JsonResponse({"due": [_task_json(t) for t in qs]})


@csrf_exempt
@require_POST
def log_reminder(request):
    """Doctor dismissed the incoming call. We log it and the task will come back after the repeat gap."""
    task = Task.objects.filter(id=_body(request).get("task_id")).first()
    if task:
        Reminder.objects.create(task=task, answered=False)
        task.last_reminded_at = timezone.now()
        task.save()
    return JsonResponse({"ok": True})