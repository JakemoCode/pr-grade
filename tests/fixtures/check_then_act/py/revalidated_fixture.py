# The state is read and checked again after the await, so the first check is not what the write relies on.
async def apply(repo, item_id):
    first = repo.get(item_id)
    if not first:
        return
    await repo.refresh(item_id)
    current = repo.get(item_id)
    if not current or current.done:
        return
    repo.update(item_id, done=True)
