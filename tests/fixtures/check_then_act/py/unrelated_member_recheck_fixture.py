# A member flag checked after the await says nothing about the row read before it.
class Worker:
    def __init__(self, repo):
        self.closed = False
        self.repo = repo

    async def run(self, item_id):
        row = self.repo.get(item_id)
        if row:  # expect: check
            return
        await self.repo.sync(item_id)  # expect: gap await
        if self.closed:
            return
        self.repo.update(item_id)  # expect: write
