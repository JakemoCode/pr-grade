# A mutator called through a subscript of a member writes that member.
class Batcher:
    def __init__(self):
        self.pending = {}

    async def add_item(self, key, value):
        if key not in self.pending:  # expect: check
            return
        await self.flush_soon()  # expect: gap await
        self.pending[key].append(value)  # expect: write

    async def flush_soon(self):
        pass
