from django.db import models


class Doctor(models.Model):
    PRESENCE = [("in_room", "In room"), ("away", "Away")]

    name = models.CharField(max_length=100)
    specialty = models.CharField(max_length=100)
    room = models.CharField(max_length=20)
    floor = models.IntegerField()
    presence = models.CharField(max_length=10, choices=PRESENCE, default="in_room")
    calendar_id = models.CharField(max_length=200, blank=True, help_text="Google Calendar ID (test calendar)")
    fee = models.IntegerField(default=0, help_text="Consultation fee, 0 = not set")

    def __str__(self):
        return self.name


class WorkingHour(models.Model):
    doctor = models.ForeignKey(Doctor, on_delete=models.CASCADE, related_name="hours")
    weekday = models.IntegerField(help_text="0 = Monday ... 6 = Sunday")
    start = models.TimeField()
    end = models.TimeField()


class Appointment(models.Model):
    doctor = models.ForeignKey(Doctor, on_delete=models.CASCADE)
    patient_name = models.CharField(max_length=100)
    start = models.DateTimeField()
    end = models.DateTimeField()
    purpose = models.CharField(max_length=200, blank=True)
    status = models.CharField(max_length=10, default="booked")  # booked / cancelled
    calendar_event_id = models.CharField(max_length=200, blank=True)


class Task(models.Model):
    doctor = models.ForeignKey(Doctor, on_delete=models.CASCADE)
    appointment = models.ForeignKey(Appointment, on_delete=models.CASCADE, null=True, blank=True)
    title = models.CharField(max_length=200)
    due_at = models.DateTimeField()
    status = models.CharField(max_length=10, default="pending")  # pending / done / cancelled / expired
    snoozed_until = models.DateTimeField(null=True, blank=True)
    last_reminded_at = models.DateTimeField(null=True, blank=True)


class Reminder(models.Model):
    task = models.ForeignKey(Task, on_delete=models.CASCADE)
    sent_at = models.DateTimeField(auto_now_add=True)
    answered = models.BooleanField(default=False)


class CallSession(models.Model):
    role = models.CharField(max_length=10)  # patient / doctor
    doctor = models.ForeignKey(Doctor, on_delete=models.SET_NULL, null=True, blank=True)
    messages = models.JSONField(default=list)  # full transcript, including tool calls
    created_at = models.DateTimeField(auto_now_add=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    summary = models.CharField(max_length=300, blank=True)  # one line written by the LLM when the call ends
    outcome = models.CharField(max_length=20, blank=True)  # booked / cancelled / moved / emergency / task / info
    metrics = models.JSONField(default=list)  # one entry per turn: stt_ms, first_ms, llm_ms, model, fallback