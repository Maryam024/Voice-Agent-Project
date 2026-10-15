from django.urls import path
from . import views

urlpatterns = [
    path("voice/start", views.voice_start),
    path("voice/turn", views.voice_turn),
    path("voice/transcript/<int:session_id>", views.transcript),
    path("doctors", views.doctors),
    path("doctors/<int:doctor_id>/presence", views.set_presence),
    path("appointments", views.appointments),
    path("tasks", views.tasks),
    path("tasks/<int:task_id>/done", views.task_done),
    path("reminders/due", views.due_reminders),
    path("reminders/log", views.log_reminder),
]
