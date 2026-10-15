"""Doctor schedule logic: working hours, free slots, and where the doctor is right now."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from django.conf import settings
from django.utils import timezone

from .models import Appointment

TZ = ZoneInfo(settings.TIME_ZONE)
SLOT = 30  # minutes per appointment


def clinic_now():
    return timezone.now().astimezone(TZ)


def label(dt):
    return dt.astimezone(TZ).strftime("%a %d %b, %H:%M")


def parse_local(iso):
    """'2026-10-14T15:00' -> aware datetime in clinic time zone."""
    dt = datetime.fromisoformat(iso)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=TZ)
    return dt.astimezone(TZ)


def hours_for(doctor, day):
    """Working periods of a doctor on a given date, as (start, end) datetimes."""
    return [
        (datetime.combine(day, h.start, TZ), datetime.combine(day, h.end, TZ))
        for h in doctor.hours.filter(weekday=day.weekday())
    ]


def overlapping(doctor, start, end, exclude_id=None):
    qs = Appointment.objects.filter(doctor=doctor, status="booked", start__lt=end, end__gt=start)
    return qs.exclude(id=exclude_id) if exclude_id else qs


def is_free(doctor, start, end, exclude_id=None):
    """Returns (True, "") or (False, reason)."""
    if start < clinic_now():
        return False, "That time is in the past."
    if not any(s <= start and end <= e for s, e in hours_for(doctor, start.date())):
        return False, f"{doctor.name} does not work at that time."
    if overlapping(doctor, start, end, exclude_id).exists():
        return False, "That slot is already booked."
    return True, ""


def free_slots(doctor, day):
    slots = []
    for s, e in hours_for(doctor, day):
        t = s
        while t + timedelta(minutes=SLOT) <= e:
            if is_free(doctor, t, t + timedelta(minutes=SLOT))[0]:
                slots.append(t.strftime("%H:%M"))
            t += timedelta(minutes=SLOT)
    return slots


def next_free(doctor, days=7):
    """First free slot in the next days, as 'YYYY-MM-DDTHH:MM' (or None)."""
    today = clinic_now().date()
    for i in range(days):
        day = today + timedelta(days=i)
        slots = free_slots(doctor, day)
        if slots:
            return f"{day.isoformat()}T{slots[0]}"
    return None


def doctor_status(doctor):
    """Where is the doctor, and can they see someone right now?"""
    now = clinic_now()
    working = any(s <= now <= e for s, e in hours_for(doctor, now.date()))
    current = overlapping(doctor, now, now + timedelta(seconds=1)).first()
    place = f"Room {doctor.room}, floor {doctor.floor}"

    if doctor.presence == "away":
        state, msg = "away", f"{doctor.name} is not in the clinic right now."
    elif not working:
        state, msg = "off_hours", f"{doctor.name} is in {place} but is outside working hours."
    elif current:
        state = "in_room_busy"
        msg = f"{doctor.name} is in {place} but is with a patient until {current.end.astimezone(TZ):%H:%M}."
    else:
        state, msg = "available", f"{doctor.name} is in {place} and is free now."

    return {
        "state": state,
        "message": msg,
        "room": doctor.room,
        "floor": doctor.floor,
        "next_free_slot": next_free(doctor) if state != "available" else None,
    }
