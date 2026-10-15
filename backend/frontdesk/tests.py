from datetime import time, timedelta
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from . import availability as av
from . import agent, llm, reminders, speech, tools
from .models import Appointment, CallSession, Doctor, Reminder, Task, WorkingHour


def tomorrow_at(hour):
    return (av.clinic_now() + timedelta(days=1)).replace(hour=hour, minute=0, second=0, microsecond=0)


class FrontDeskTests(TestCase):
    def setUp(self):
        self.doc = Doctor.objects.create(name="Dr. Ayesha Khan", specialty="Cardiology", room="204", floor=2)
        for day in range(7):
            WorkingHour.objects.create(doctor=self.doc, weekday=day, start=time(9, 0), end=time(17, 0))
        self.session = CallSession.objects.create(role="patient")

    def book(self, hour=15, confirmed=True):
        return tools.book_appointment({"session": self.session}, self.doc.id, "Ali", tomorrow_at(hour).isoformat(), "checkup", confirmed)

    def test_booking_creates_appointment_and_task(self):
        result = self.book()
        self.assertTrue(result["booked"])
        self.assertEqual(Appointment.objects.count(), 1)
        self.assertEqual(Task.objects.get().status, "pending")
        self.assertTrue(Appointment.objects.get().calendar_event_id.startswith("local-"))

    def test_needs_confirmation(self):
        self.assertIn("error", self.book(confirmed=False))
        self.assertEqual(Appointment.objects.count(), 0)

    def test_no_double_booking(self):
        self.book()
        self.assertIn("error", self.book())

    def test_outside_working_hours(self):
        self.assertIn("error", self.book(hour=3))

    def test_fuzzy_doctor_name(self):
        result = tools.find_doctor({}, "Dr Aisha Kan")
        self.assertEqual(result["doctors"][0]["name"], "Dr. Ayesha Khan")

    def test_free_slots_skip_booked(self):
        self.book(hour=10)
        ranges = tools.check_availability({}, self.doc.id, tomorrow_at(10).date().isoformat())["free_ranges"]
        self.assertEqual(ranges, "09:00-10:00, 10:30-17:00")  # 10:00 is booked, the rest is free

    def test_in_room_but_busy_message(self):
        now = timezone.now()
        Appointment.objects.create(doctor=self.doc, patient_name="X", start=now - timedelta(minutes=5), end=now + timedelta(minutes=25))
        with patch.object(av, "hours_for", return_value=[(av.clinic_now() - timedelta(hours=1), av.clinic_now() + timedelta(hours=1))]):
            status = av.doctor_status(self.doc)
        self.assertEqual(status["state"], "in_room_busy")
        self.assertIn("Room 204", status["message"])
        self.assertIn("floor 2", status["message"])

    def test_reminder_repeats_until_done(self):
        task = Task.objects.create(doctor=self.doc, title="t", due_at=timezone.now() - timedelta(minutes=1))
        self.assertEqual(reminders.due_tasks().count(), 1)
        task.last_reminded_at = timezone.now()
        task.save()
        self.assertEqual(reminders.due_tasks().count(), 0)          # just reminded
        task.last_reminded_at = timezone.now() - timedelta(minutes=10)
        task.save()
        self.assertEqual(reminders.due_tasks().count(), 1)          # comes back
        tools.snooze_task({"session": CallSession(role="doctor", doctor=self.doc)}, task.id, 60)
        self.assertEqual(reminders.due_tasks().count(), 0)          # snoozed
        tools.complete_task({"session": CallSession(role="doctor", doctor=self.doc)}, task.id)
        self.assertEqual(reminders.due_tasks().count(), 0)          # done

    def test_move_appointment_updates_task(self):
        self.book(hour=10)
        appt = Appointment.objects.get()
        ctx = {"session": CallSession(role="doctor", doctor=self.doc)}
        result = tools.move_appointment(ctx, appt.id, tomorrow_at(14).isoformat(), True)
        self.assertIn("moved_to", result)
        appt.refresh_from_db()
        self.assertEqual(appt.start.astimezone(av.TZ).hour, 14)

    def test_voice_flow_with_fake_llm(self):
        """Start call -> say something -> LLM calls a tool -> LLM answers. STT, LLM and TTS are faked."""
        fake = [
            {"role": "assistant", "content": "", "tool_calls": [
                {"id": "1", "type": "function", "function": {"name": "find_doctor", "arguments": '{"query": "Khan"}'}}]},
            {"role": "assistant", "content": "Dr. Khan is in room 204 on floor 2."},
        ]
        with patch("frontdesk.llm.chat", side_effect=fake), patch("frontdesk.tts.synthesize", return_value=None):
            start = self.client.post("/api/voice/start", {"role": "patient"}, content_type="application/json").json()
            self.assertIn("I'm an AI", start["text"])  # disclosure
            turn = self.client.post("/api/voice/turn", {"session_id": start["session_id"], "text": "Where is Dr Khan?"}).json()
        self.assertIn("room 204", turn["reply"])
        said = self.client.get(f"/api/voice/transcript/{start['session_id']}").json()["transcript"]
        self.assertEqual([m["who"] for m in said], ["agent", "user", "agent"])

    def test_answering_reminder_call_logs_it(self):
        task = Task.objects.create(doctor=self.doc, title="Prep", due_at=timezone.now() - timedelta(minutes=1))
        with patch("frontdesk.tts.synthesize", return_value=None):
            self.client.post("/api/voice/start", {"role": "doctor", "doctor_id": self.doc.id, "task_id": task.id}, content_type="application/json")
        self.assertEqual(Reminder.objects.filter(task=task, answered=True).count(), 1)

    def test_reschedule_moves_the_old_meeting(self):
        self.book(hour=15)
        ctx = {"session": self.session}
        found = tools.find_my_appointments(ctx, "Ali")["appointments"]
        self.assertEqual(len(found), 1)
        result = tools.reschedule_appointment(ctx, found[0]["appointment_id"], "Ali", tomorrow_at(11).isoformat(), True)
        self.assertIn("moved_to", result)
        self.assertEqual(Appointment.objects.count(), 1)  # moved, not a second meeting
        self.assertEqual(Appointment.objects.get().start.astimezone(av.TZ).hour, 11)

    def test_reschedule_needs_the_right_name(self):
        self.book(hour=15)
        appt = Appointment.objects.get()
        result = tools.reschedule_appointment({"session": self.session}, appt.id, "Zara", tomorrow_at(11).isoformat(), True)
        self.assertIn("error", result)
        self.assertEqual(Appointment.objects.get().start.astimezone(av.TZ).hour, 15)

    def test_booking_twice_for_same_patient_is_refused(self):
        self.book(hour=10)
        result = self.book(hour=11)
        self.assertIn("reschedule_appointment", result["error"])
        self.assertEqual(Appointment.objects.count(), 1)

    def test_spoken_text(self):
        self.assertEqual(
            speech.spoken("Booked for 2026-10-16T16:30 Asia/Karachi."),
            "Booked for Friday 16 October at 4:30 PM Karachi time.",
        )

    def test_reply_is_short_and_not_repeated(self):
        self.assertEqual(llm.shorten("Sure. Sure. Hello there. Third. Fourth."), "Sure. Hello there. Third.")  # repeats removed, 3 sentences at most

class NewFeatureTests(TestCase):
    def setUp(self):
        self.doc = Doctor.objects.create(name="Dr. Ayesha Khan", specialty="Cardiology", room="204", floor=2)
        for day in range(7):
            WorkingHour.objects.create(doctor=self.doc, weekday=day, start=time(0, 0), end=time(23, 59))
        self.patient = CallSession.objects.create(role="patient")
        self.doctor_session = CallSession.objects.create(role="doctor", doctor=self.doc)

    # ----- voice to-dos and reminders -----
    def test_create_task_and_reminder(self):
        ctx = {"session": self.doctor_session}
        due = (av.clinic_now() + timedelta(hours=2)).replace(microsecond=0).isoformat()
        result = tools.create_task(ctx, "Call the lab", due)
        self.assertTrue(result["created"])
        self.assertEqual(Task.objects.get(id=result["task_id"]).doctor, self.doc)
        self.assertEqual(reminders.due_tasks().count(), 0)          # not due yet
        tools.set_reminder(ctx, 20, "Stretch")
        self.assertEqual(Task.objects.count(), 2)
        self.assertIn("error", tools.create_task(ctx, "Old", (av.clinic_now() - timedelta(hours=3)).isoformat()))
        self.assertIn("error", tools.create_task(ctx, "  "))
        self.assertIn("error", tools.set_reminder(ctx, 0))

    def test_task_without_time_is_due_now(self):
        tools.create_task({"session": self.doctor_session}, "Sign forms")
        self.assertEqual(reminders.due_tasks().count(), 1)

    # ----- task expiry and order -----
    def test_old_meeting_task_expires_and_pending_sorts_first(self):
        now = timezone.now()
        old = Appointment.objects.create(doctor=self.doc, patient_name="Old", start=now - timedelta(hours=2), end=now - timedelta(hours=1))
        Task.objects.create(doctor=self.doc, appointment=old, title="old", due_at=now - timedelta(hours=3))
        Task.objects.create(doctor=self.doc, title="done one", due_at=now, status="done")
        Task.objects.create(doctor=self.doc, title="pending one", due_at=now + timedelta(hours=1))
        self.assertEqual(reminders.expire_old_tasks(), 1)
        self.assertEqual(Task.objects.get(title="old").status, "expired")
        self.assertEqual(reminders.due_tasks().count(), 0)
        self.assertEqual(reminders.ordered(Task.objects.all())[0].title, "pending one")

    def test_task_without_meeting_never_expires(self):
        Task.objects.create(doctor=self.doc, title="x", due_at=timezone.now() - timedelta(days=3))
        self.assertEqual(reminders.expire_old_tasks(), 0)

    # ----- emergency -----
    def test_emergency_phrase_skips_the_llm_and_ends_the_call(self):
        with patch("frontdesk.llm.chat", side_effect=AssertionError("LLM must not run")):
            said = []
            agent.run_turn(self.patient, "my father can't breathe please help", said.append)
        self.assertIn("emergency", said[0])
        self.assertTrue(self.patient.end_call)
        self.patient.refresh_from_db()
        self.assertEqual(self.patient.outcome, "emergency")

    def test_past_heart_attack_is_not_an_emergency(self):
        reply = {"role": "assistant", "content": "Sure, which doctor?"}
        with patch("frontdesk.llm.chat", return_value=reply) as chat:
            agent.run_turn(self.patient, "my father had a heart attack last year, I want a cardiology visit", lambda s: None)
        chat.assert_called()
        self.assertFalse(getattr(self.patient, "end_call", False))

    def test_emergency_tool_ends_the_call(self):
        result = tools.run("emergency_help", "{}", {"session": self.patient})
        self.assertTrue(result["emergency"])
        self.assertTrue(self.patient.end_call)

    # ----- general questions and goodbye -----
    def test_general_question_is_answered_without_tools(self):
        def fake_chat(messages, tools, on_sentence, *args, **kwargs):
            on_sentence("Paris is the capital of France.")  # the real llm.chat streams sentences like this
            return {"role": "assistant", "content": "Paris is the capital of France."}

        said = []
        with patch("frontdesk.llm.chat", side_effect=fake_chat):
            agent.run_turn(self.patient, "what is the capital of France?", said.append)
        self.assertEqual(said, ["Paris is the capital of France."])
        self.assertFalse(getattr(self.patient, "end_call", False))

    def test_prompt_allows_general_questions_and_forbids_anything_else(self):
        prompt = agent._system_prompt(self.patient)
        self.assertIn("Never refuse", prompt)
        self.assertIn('"anything else?"', prompt)
        self.assertNotIn("you can only help with appointments, doctors", prompt)

    def test_end_call_tool(self):
        tools.run("end_call", "{}", {"session": self.patient})
        self.assertTrue(self.patient.end_call)

    # ----- summaries and metrics -----
    def test_finish_call_writes_outcome_and_summary(self):
        self.patient.messages = [
            {"role": "assistant", "content": "Hello"},
            {"role": "user", "content": "Book Dr Khan tomorrow at 3, I am Ali"},
            {"role": "tool", "tool_call_id": "1", "content": '{"booked": true, "doctor": "Dr. Ayesha Khan", "when": "Tue 20 Oct, 15:00"}'},
            {"role": "assistant", "content": "Booked."},
        ]
        with patch("frontdesk.llm.chat", return_value={"role": "assistant", "content": "Ali booked Dr Khan for Tuesday 3 PM."}):
            agent.finish_call(self.patient)
        self.patient.refresh_from_db()
        self.assertEqual(self.patient.outcome, "booked")
        self.assertEqual(self.patient.summary, "Ali booked Dr Khan for Tuesday 3 PM.")
        self.assertIsNotNone(self.patient.ended_at)

    def test_summary_falls_back_when_llm_fails(self):
        self.patient.messages = [
            {"role": "user", "content": "cancel"},
            {"role": "tool", "tool_call_id": "1", "content": '{"cancelled": true, "patient": "Ali", "doctor": "Dr. Ayesha Khan"}'},
        ]
        with patch("frontdesk.llm.chat", side_effect=RuntimeError("down")):
            agent.finish_call(self.patient)
        self.assertIn("cancelled", self.patient.summary)

    def test_silent_call_gets_no_llm_call(self):
        self.patient.messages = [{"role": "assistant", "content": "Hello"}]
        with patch("frontdesk.llm.chat", side_effect=AssertionError("no LLM for a silent call")):
            agent.finish_call(self.patient)
        self.assertIn("without speaking", self.patient.summary)

    def test_metrics_page_numbers(self):
        agent.log_turn(self.patient, {"stt_ms": 400, "first_ms": 800, "llm_ms": 1200}, {"model": "m1", "fallback": False})
        agent.log_turn(self.patient, {"stt_ms": 600, "first_ms": 1000, "llm_ms": 1400}, {"model": "m2", "fallback": True})
        self.patient.outcome = "booked"
        self.patient.save()
        from . import realtime
        m = realtime._metrics()
        self.assertEqual((m["calls_today"], m["bookings_today"], m["turns"]), (2, 1, 2))  # 2 calls: patient + doctor session
        self.assertEqual((m["avg_stt_ms"], m["fallback_pct"]), (500, 50))
        calls = realtime._calls()
        self.assertEqual(len(calls), 2)

    # ----- manual buttons -----
    def test_manual_cancel_and_move(self):
        from . import realtime
        start = (av.clinic_now() + timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0)
        appt = Appointment.objects.create(doctor=self.doc, patient_name="Ali", start=start, end=start + timedelta(minutes=30))
        task = Task.objects.create(doctor=self.doc, appointment=appt, title="t", due_at=timezone.now())
        moved = realtime._move_appointment(appt.id, (start + timedelta(hours=2)).replace(tzinfo=None).isoformat())
        self.assertEqual(moved, {"ok": True})
        appt.refresh_from_db()
        self.assertEqual(appt.start.astimezone(av.TZ).hour, 12)
        self.assertIn("error", realtime._move_appointment(appt.id, "not a date"))
        self.assertEqual(realtime._cancel_appointment(appt.id), {"ok": True})
        task.refresh_from_db()
        self.assertEqual((appt.__class__.objects.get(id=appt.id).status, task.status), ("cancelled", "cancelled"))
        self.assertIn("error", realtime._cancel_appointment(appt.id))


class TokenSavingTests(TestCase):
    def test_history_window_keeps_recent_turns_and_never_splits_a_tool_call(self):
        msgs = [{"role": "assistant", "content": "hi"}]
        for i in range(10):
            msgs += [{"role": "user", "content": f"u{i}"},
                     {"role": "assistant", "content": "", "tool_calls": [{"id": str(i)}]},
                     {"role": "tool", "tool_call_id": str(i), "content": "{}"},
                     {"role": "assistant", "content": f"a{i}"}]
        window = agent._window(msgs)
        self.assertEqual(window[0]["content"], "u4")                       # the last 6 caller turns
        self.assertEqual(sum(1 for m in window if m["role"] == "user"), 6)
        self.assertEqual(agent._window(msgs[:5]), msgs[:5])                # a short call is sent whole

    def test_short_rate_limit_is_waited_out_long_one_is_not(self):
        import httpx
        import openai

        def limit(text, headers=None):
            resp = httpx.Response(429, headers=headers or {}, request=httpx.Request("POST", "http://x"))
            return openai.RateLimitError(text, response=resp, body=None)

        self.assertEqual(llm._retry_wait(limit("Please try again in 5.4375s.")), 5.4375)
        self.assertEqual(llm._retry_wait(limit("slow", {"retry-after": "3"})), 3.0)
        self.assertIsNone(llm._retry_wait(limit("Please try again in 90s.")))
        self.assertIsNone(llm._retry_wait(RuntimeError("other")))

    def test_chat_retries_once_after_a_short_rate_limit(self):
        import httpx
        import openai
        resp = httpx.Response(429, headers={"retry-after": "1"}, request=httpx.Request("POST", "http://x"))
        error = openai.RateLimitError("limit", response=resp, body=None)
        good = {"role": "assistant", "content": "ok"}
        with patch.dict("os.environ", {"LLM_CHAIN": "", "LLM_MODEL": "m"}), \
             patch("frontdesk.llm._chat_once", side_effect=[error, good]) as once, patch("frontdesk.llm.time.sleep") as nap:
            self.assertEqual(llm.chat([{"role": "user", "content": "hi"}]), good)
        self.assertEqual(once.call_count, 2)
        nap.assert_called_once()