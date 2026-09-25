# Each turn of an async for waits, so a check before it goes stale.
async def consume(repo, stream, item_id):
    row = repo.get(item_id)
    if row:  # expect: check
        return
    async for event in stream:  # expect: gap for-await
        repo.update(item_id, event)  # expect: write
