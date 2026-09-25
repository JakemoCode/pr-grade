# A finally block runs on the normal path too, so its write is the guarded write.
async def finish(repo, item_id):
    row = repo.get(item_id)
    if row:  # expect: check
        return
    try:
        await repo.sync(item_id)  # expect: gap await
    finally:
        repo.update(item_id)  # expect: write
