"""Which tasks should the agent remind about right now?"""
from datetime import timedelta

from django.conf import settings
from django.db.models import Case, IntegerField, Q, Value, When
from django.utils import timezone

from .models import Task


def due_tasks(doctor_id=None):
    """Pending tasks that are due, not snoozed, and not reminded in the last few minutes.
    A task keeps coming back until it is marked done or snoozed."""
    now = timezone.now()
    repeat_gap = now - timedelta(minutes=settings.REMINDER_REPEAT_MINUTES)
    qs = (
        Task.objects.filter(status="pending", due_at__lte=now)
        .filter(Q(snoozed_until__isnull=True) | Q(snoozed_until__lte=now))
        .filter(Q(last_reminded_at__isnull=True) | Q(last_reminded_at__lte=repeat_gap))
    )
    if doctor_id:
        qs = qs.filter(doctor_id=doctor_id)
    return qs.order_by("due_at")


def expire_old_tasks():
    """A task about a meeting that already ended is useless: stop reminding about it."""
    now = timezone.now()
    return Task.objects.filter(status="pending", appointment__isnull=False, appointment__end__lt=now).update(status="expired")


def ordered(qs):
    """Pending first (by due time), then everything else. Plain order_by('status') puts 'pending' last."""
    rank = Case(When(status="pending", then=Value(0)), default=Value(1), output_field=IntegerField())
    return qs.order_by(rank, "due_at")