# A transaction nested inside the one holding the check is a savepoint: nothing lands between them.
from django.db import transaction


def build(repo, key):
    with transaction.atomic():
        if repo.find(key):
            return None
        with transaction.atomic():
            return repo.insert(key)
