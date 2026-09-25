# The check, the await and the write all sit under one lock, so no second caller runs between them.
class Cache:
    def __init__(self, lock, store):
        self._lock, self.store = lock, store

    async def fill(self, key):
        async with self._lock:
            value = self.store.get(key)
            if value is not None:
                return value
            fresh = await self.store.load(key)
            self.store.put(key, fresh)
            return fresh
