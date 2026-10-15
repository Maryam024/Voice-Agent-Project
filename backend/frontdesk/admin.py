from django.contrib import admin
from .models import Appointment, CallSession, Doctor, Reminder, Task, WorkingHour


class HourInline(admin.TabularInline):
    model = WorkingHour
    extra = 0


@admin.register(Doctor)
class DoctorAdmin(admin.ModelAdmin):
    list_display = ("name", "specialty", "room", "floor", "presence")
    inlines = [HourInline]


admin.site.register([Appointment, Task, Reminder, CallSession])
