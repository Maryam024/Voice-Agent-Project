from datetime import time, timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from frontdesk.models import Appointment, Doctor, WorkingHour

DOCTORS = [
    ("Dr. Ayesha Khan", "Cardiology", "204", 2, "in_room"),
    ("Dr. Imran Siddiqui", "General Medicine", "101", 1, "in_room"),
    ("Dr. Sara Malik", "Dermatology", "305", 3, "away"),
]


class Command(BaseCommand):
    help = "Create demo doctors, working hours and one meeting that is happening right now."

    def add_arguments(self, parser):
        parser.add_argument("--if-empty", action="store_true", help="Do nothing if doctors already exist (used at deploy start)")
        parser.add_argument("--open-all-day", action="store_true", help="Working hours 00:00-23:59 every day, handy for testing at night")

    def handle(self, *args, **options):
        if options["if_empty"] and Doctor.objects.exists():
            return  # keep the doctors that are already there
        Doctor.objects.all().delete()
        for name, specialty, room, floor, presence in DOCTORS:
            doctor = Doctor.objects.create(name=name, specialty=specialty, room=room, floor=floor, presence=presence)
            for weekday in range(7 if options["open_all_day"] else 5):  # Mon-Fri
                start, end = (time(0, 0), time(23, 59)) if options["open_all_day"] else (time(9, 0), time(17, 0))
                WorkingHour.objects.create(doctor=doctor, weekday=weekday, start=start, end=end)

        # Dr. Khan is in her room but with a patient right now (to demo "present but not available")
        now = timezone.now()
        Appointment.objects.create(
            doctor=Doctor.objects.get(name="Dr. Ayesha Khan"), patient_name="Walk-in patient",
            start=now - timedelta(minutes=10), end=now + timedelta(minutes=20), purpose="consultation",
        )
        self.stdout.write(self.style.SUCCESS("Demo data created."))