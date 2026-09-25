# A method named set does not shadow the builtin set() in another function.
class Cache:
    def set(self, key, value):
        pass


async def tags(repo, item_id):
    row = repo.get(item_id)
    if row:
        return None
    await repo.sync(item_id)
    return set([1])
