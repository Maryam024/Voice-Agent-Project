"""The dialog brain: builds the prompt, calls the LLM, runs tools, repeats until the LLM answers in words."""
import json
import re
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from . import availability as av
from . import llm, speech, tools
from .models import Appointment, Doctor, Task

MAX_STEPS = 5  # max LLM <-> tool round trips per turn
HISTORY_TURNS = 6  # only the last 6 caller turns are sent to the LLM: fewer tokens, so fewer rate-limit errors


def _window(messages):
    """The recent part of the conversation. It always starts at a caller message, so a tool call is never separated from its result."""
    starts = [i for i, m in enumerate(messages) if m["role"] == "user"]
    return messages[starts[-HISTORY_TURNS]:] if len(starts) > HISTORY_TURNS else messages

STYLE = """HOW TO SPEAK (phone call):
- 1-2 short sentences; up to 3 for a general question. No lists, markdown or emojis.
- Never repeat the caller's words or your own earlier words. Never say what you are about to do.
- Never end with "how can I help you?", "anything else?" or similar. Answer, then stop.
- Ask at most one question, only if a detail is really missing. Never re-ask what was already said.
- Say dates like "Tuesday 14 October" and times like "3 PM". Offer at most 3 times.
- If the words look garbled, ask them to repeat in a few words."""

GENERAL = """ANY TOPIC: answer anything the person asks (general knowledge, small talk, jokes, maths, date or time, general health information) directly and briefly. Never refuse and never say you only handle appointments. Health: general information only, no diagnosis or prescriptions; suggest a doctor when it matters. If you do not know, say so. Never invent facts. Use the person's language when you can. On goodbye or "that's all": call end_call, then say a short goodbye."""

PATIENT_PROMPT = """You are the AI voice front desk assistant of {clinic} (say you are an AI if asked). Now: {now} ({tz}). Next days: {days}.

""" + STYLE + """

""" + GENERAL + """

Jobs: book, move and cancel meetings; say where a doctor is; give doctor fees, clinic phone and address.
EMERGENCY now (chest pain, trouble breathing, heavy bleeding, stroke signs, unconscious person, overdose, serious accident): call emergency_help at once, book nothing, then tell them in one sentence to call that number.
Rules:
- Tool first, then words. Never talk before a tool call. Use find_doctor first; never invent doctors, rooms, floors, fees or times.
- Unclear name, date or time: ask once, never guess. Turn spoken times into ISO using the date above; visits are in working hours, so "3" means 15:00.
- check_availability: if a time was asked, say whether that exact time is free before offering others.
- Is the doctor in? get_doctor_status. If busy, say so with room and floor and offer the next free slot.
- Before booking say doctor, date, time, time zone and purpose, wait for a clear yes, get the patient's name, then book_appointment with confirmed=true.
- Move or cancel: ask the name, find_my_appointments, say the meeting back, wait for a clear yes, then reschedule_appointment (with the name) or cancel_appointment with confirmed=true.
- Fee: from find_doctor. Phone or address: get_clinic_info. "not set" means you do not have it.
- After a successful booking, move or cancel: one sentence (a booking includes room and floor), then goodbye. The call ends by itself."""

DOCTOR_PROMPT = """You are the AI voice assistant of {clinic}, talking to {doctor} (doctor_id {doctor_id}). You are an AI. Now: {now} ({tz}). Next days: {days}.

""" + STYLE + """

""" + GENERAL + """

Pending tasks:
{tasks}

Upcoming meetings:
{appointments}

- "done" -> complete_task. "snooze an hour" -> snooze_task (minutes=60).
- "remind me to call the lab at 5 PM" -> create_task (title, due_at). "remind me in 20 minutes to ..." -> set_reminder (minutes, title). No time given: create_task without due_at. Confirm in one short sentence; do not end the call.
- Move a meeting: ask the new time, check_availability, confirm in one sentence, wait for a clear yes, then move_appointment with confirmed=true.
- Cancel a meeting: say it back, wait for a clear yes, then cancel_appointment with confirmed=true.
- After a move or cancel: one sentence, then goodbye. Unclear? Ask once, never guess."""


def greeting(role, doctor=None, task=None):
    """Fixed opening line, so the AI disclosure never depends on the LLM."""
    if role == "doctor":
        if task:
            return (f"Hello {doctor.name}, this is the clinic's AI assistant. "
                    f"Your task \"{task.title}\" was due at {av.label(task.due_at)} and is still pending. "
                    "Is it done, should I snooze it, or do you want to move the meeting?")
        return f"Hello {doctor.name}, I'm the clinic's AI assistant. How can I help?"
    return (f"Hello, this is the AI voice assistant of {settings.CLINIC_NAME}. I'm an AI, not a person, "
            "and this call is transcribed. How can I help you today?")


def stt_hint():
    """Only the names. Extra words here make Whisper invent them when it hears silence."""
    names = ", ".join(Doctor.objects.values_list("name", flat=True))
    return f"{names}." if names else ""


def _days(now):
    return ", ".join(f"{now + timedelta(days=i):%a %d %b}" for i in range(10))


def _system_prompt(session):
    now = av.clinic_now()
    common = {"clinic": settings.CLINIC_NAME, "now": now.strftime("%A %Y-%m-%d %H:%M"), "tz": av.TZ, "days": _days(now)}
    if session.role == "patient":
        return PATIENT_PROMPT.format(**common)

    doctor = session.doctor
    tasks = Task.objects.filter(doctor=doctor, status="pending")
    appts = Appointment.objects.filter(doctor=doctor, status="booked", start__gte=timezone.now())
    return DOCTOR_PROMPT.format(
        doctor=doctor.name, doctor_id=doctor.id,
        tasks="\n".join(f"- task {t.id}: {t.title} (meeting {t.appointment_id}, due {av.label(t.due_at)})" for t in tasks) or "none",
        appointments="\n".join(f"- meeting {a.id}: {a.patient_name}, {av.label(a.start)}, {a.purpose}" for a in appts) or "none",
        **common,
    )


# Clear, present-tense emergencies. Checked BEFORE the LLM so safety never depends on the model.
# Things like "my father had a heart attack last year" must NOT match, so these are all "right now" phrases.
_EMERGENCY = re.compile(
    r"(can'?t|cannot|can not|unable to) breathe|not breathing|stopped breathing|is unconscious|unconscious right now"
    r"|(having|is having|he is having|she is having) (a )?(heart attack|stroke|seizure)"
    r"|bleeding (heavily|badly|a lot|too much|won'?t stop)|severe bleeding|(took|taken) (an )?overdose|choking"
    r"|kill myself|want to die|suicid",
    re.I,
)


def run_turn(session, user_text, on_sentence=None, cancel=None, info=None):
    """One conversation turn. Returns the words the agent should say.
    on_sentence(text) is called for each sentence as soon as it is ready (live speech).
    cancel is an Event: when set, the caller interrupted and we stop at once."""
    session.messages.append({"role": "user", "content": user_text})
    ctx = {"session": session}
    reply = {}
    spoken = []

    def stopped():
        return cancel is not None and cancel.is_set()

    def say(sentence):
        sentence = speech.spoken(sentence)  # "16:30" -> "4:30 PM", so the voice reads it naturally
        spoken.append(sentence)
        if on_sentence:
            on_sentence(sentence)

    if session.role == "patient" and _EMERGENCY.search(user_text):
        number = settings.EMERGENCY_NUMBER
        text = (f"This sounds like an emergency. Please call {number} right now or go to the nearest emergency room. "
                "I cannot book appointments for emergencies.")
        say(text)
        session.messages.append({"role": "assistant", "content": text})
        session.outcome = "emergency"
        session.end_call = True
        session.save()
        return text

    for _ in range(MAX_STEPS):
        messages = [{"role": "system", "content": _system_prompt(session)}] + _window(session.messages)
        reply = llm.chat(messages, tools.SCHEMAS[session.role], say, cancel, info)
        if stopped():
            reply.pop("tool_calls", None)  # a tool call that never ran must not stay in the history
            if reply.get("content"):
                session.messages.append(reply)
            break
        session.messages.append(reply)
        if not reply.get("tool_calls"):
            break
        for call in reply["tool_calls"]:
            result = tools.run(call["function"]["name"], call["function"]["arguments"], ctx)
            session.messages.append({"role": "tool", "tool_call_id": call["id"], "content": json.dumps(result)})

    if getattr(session, "emergency", False):
        session.outcome = "emergency"
    session.save()
    ending = getattr(session, "end_call", False)
    if not spoken:
        say("Done. Goodbye." if ending else "Sorry, could you say that again?")
    return reply.get("content") or "Done."


def transcript(session):
    """Only the spoken lines (hides tool calls)."""
    return [
        {"who": "agent" if m["role"] == "assistant" else "user", "text": m["content"]}
        for m in session.messages
        if m["role"] in ("user", "assistant") and m.get("content")
    ]


# ---------- after the call: metrics, outcome, one-line summary ----------

def log_turn(session, timings, info):
    """Keep the speed numbers and the model name of every turn, for the metrics page."""
    session.metrics = (session.metrics or []) + [{
        "stt_ms": timings.get("stt_ms"), "first_ms": timings.get("first_ms"), "llm_ms": timings.get("llm_ms"),
        "model": (info or {}).get("model", ""), "fallback": bool((info or {}).get("fallback")),
    }]
    session.save(update_fields=["metrics"])


def _results(session):
    """All tool results of this call that did not fail."""
    out = []
    for m in session.messages:
        if m.get("role") != "tool":
            continue
        try:
            data = json.loads(m.get("content") or "{}")
        except ValueError:
            continue
        if isinstance(data, dict) and "error" not in data:
            out.append(data)
    return out


def _outcome(session):
    results = _results(session)
    for key, name in (("emergency", "emergency"), ("booked", "booked"), ("moved_to", "moved"), ("cancelled", "cancelled"),
                      ("created", "task"), ("reminder_set", "task"), ("done", "task"), ("snoozed_until", "task")):
        if any(key in r for r in results):
            return name
    return "info"


def _fallback_summary(session, outcome):
    results = _results(session)
    if outcome == "booked":
        r = next(r for r in results if "booked" in r)
        return f"Appointment booked with {r.get('doctor')} for {r.get('when')}."
    if outcome == "moved":
        r = next(r for r in results if "moved_to" in r)
        return f"Appointment of {r.get('patient')} with {r.get('doctor')} moved to {r.get('moved_to')}."
    if outcome == "cancelled":
        r = next(r for r in results if "cancelled" in r)
        return f"Appointment of {r.get('patient')} with {r.get('doctor')} cancelled."
    if outcome == "emergency":
        return "Caller described an emergency and was told to call emergency services."
    return f"{session.role.title()} call. Outcome: {outcome}."


def finish_call(session):
    """Called once when the call ends: store the outcome and a one-line summary written by the LLM."""
    if session.ended_at:
        return
    session.ended_at = timezone.now()
    outcome = "emergency" if session.outcome == "emergency" else _outcome(session)
    session.outcome = outcome
    lines = transcript(session)
    if not any(l["who"] == "user" for l in lines):
        session.summary = "The caller hung up without speaking."
        return session.save()
    text = "\n".join(f"{'Caller' if l['who'] == 'user' else 'Agent'}: {l['text']}" for l in lines)[-3000:]
    try:
        reply = llm.chat([
            {"role": "system", "content": "Summarise this clinic phone call in ONE plain sentence of at most 20 words: "
             "who called, what they wanted and the result. No quotes, no preamble."},
            {"role": "user", "content": text},
        ])
        session.summary = (reply.get("content") or "").strip()[:300] or _fallback_summary(session, outcome)
    except Exception as e:
        print("call summary failed:", e)
        session.summary = _fallback_summary(session, outcome)
    session.save()