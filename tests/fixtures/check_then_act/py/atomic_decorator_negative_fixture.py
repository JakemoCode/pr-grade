# A function decorated to run in a transaction holds its check and its nested savepoint in one.
from django.db import transaction


@transaction.atomic
def build(repo, key):
    if repo.find(key):
        return None
    with transaction.atomic():
        return repo.insert(key)
