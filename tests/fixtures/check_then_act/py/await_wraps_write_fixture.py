# The await is the write itself, not something between the check and the write.
async def store(repo, item_id):
    current = repo.get(item_id)
    if current:
        return
    await repo.insert(item_id)
