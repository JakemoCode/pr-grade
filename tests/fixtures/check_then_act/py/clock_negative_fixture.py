# A clock read is not shared state: a value built from timezone.now() is not a stale read.
from django.db import transaction
from django.utils import timezone


def record(response, app):
    event = Event(created_at=response.time or timezone.now())
    with transaction.atomic():
        event, error = deduplicate(event, app)
        if error:
            return error
        event.save()
    return None
