# A try body that returns still runs its finally, so a write there comes after the await.
async def finish(repo, item_id):
    row = repo.get(item_id)
    if row:  # expect: check
        return
    try:
        await repo.sync(item_id)  # expect: gap await
        return
    finally:
        repo.update(item_id)  # expect: write
