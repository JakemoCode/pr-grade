# The await is inside the check's own condition.
async def create(repo, item_id):
    if await repo.exists(item_id):
        return
    repo.insert(item_id)
