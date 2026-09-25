# An await on a path that returns cannot come between the check and a write after that path.
async def refresh(repo, item_id, fast):
    row = repo.get(item_id)
    if row:
        return
    if fast:
        await repo.sync(item_id)
        return
    repo.update(item_id)
