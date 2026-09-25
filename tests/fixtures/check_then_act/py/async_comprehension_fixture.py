# An async comprehension waits on every item, so a check before it goes stale.
async def collect(repo, stream, item_id):
    row = repo.get(item_id)
    if row:  # expect: check
        return
    events = [event async for event in stream]  # expect: gap for-await
    repo.update(item_id, events)  # expect: write
