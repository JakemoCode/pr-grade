# A check outside the transaction guards a write inside it; the second method re-checks inside.
from django.db import transaction


class Builder:
    def __init__(self, repo):
        self.repo = repo

    def build(self, key):
        found = self.repo.find(key)
        if found:  # expect: check
            return found
        with transaction.atomic():  # expect: gap transaction
            return self.repo.insert(key)  # expect: write

    def build_rechecked(self, key):
        found = self.repo.find(key)
        if found:
            return found
        with transaction.atomic():
            if self.repo.find(key):
                return None
            return self.repo.insert(key)
