# Taking the lock after the check does not help: the wait for the lock is itself the gap.
class Cache:
    def __init__(self, lock, store):
        self._lock, self.store = lock, store

    async def fill(self, key):
        value = self.store.get(key)
        if value is not None:  # expect: check
            return value
        async with self._lock:  # expect: gap await
            self.store.put(key, 'fresh')  # expect: write
