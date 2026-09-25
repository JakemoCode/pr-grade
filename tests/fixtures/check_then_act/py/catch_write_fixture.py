# Failure bookkeeping in an except block is not the write the check guards.
async def sync(repo, item_id):
    if not repo.has(item_id):
        return
    try:
        await repo.pull(item_id)
    except Exception as error:
        repo.record_error(item_id, error)
