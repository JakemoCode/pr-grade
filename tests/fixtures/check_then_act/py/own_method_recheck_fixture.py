# A recheck through the object's own method cannot be told from one that reads an unrelated flag, so
# the window stays listed for the grader to clear.
class Service:
    def __init__(self, repo, graph):
        self.repo, self.graph = repo, graph

    async def apply(self, item_id):
        item = self.repo.get(item_id)
        if item.status != 'pending':  # expect: check
            return item
        await self.graph.project(item_id)  # expect: gap await
        after = self.stored(item_id)
        if after.status != 'pending':
            return after
        self.repo.mark_applied(item_id)  # expect: write

    def stored(self, item_id):
        return self.repo.get(item_id)
