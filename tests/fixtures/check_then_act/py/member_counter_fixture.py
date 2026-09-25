# A counter checked before the await and raised after it bounds nothing. An unrelated member check
# in save() must not pair with a store write.
class Limiter:
    def __init__(self, limit, store):
        self.inflight = 0
        self.closed = False
        self.limit, self.store = limit, store

    async def run(self, task):
        if self.inflight >= self.limit:  # expect: check
            return
        await task()  # expect: gap await
        self.inflight += 1  # expect: write

    async def save(self, value):
        if self.closed:
            return
        await self.store.prepare(value)
        self.store.append(value)
