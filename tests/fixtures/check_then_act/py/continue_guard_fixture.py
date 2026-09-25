# A guard that exits by continue covers the rest of its loop, not the code after the loop.
async def drain(repo, ids):
    for item_id in ids:
        value = repo.get(item_id)
        if not value:  # expect: check
            continue
        await repo.sync(item_id)  # expect: gap await
        repo.update(item_id)  # expect: write


async def after_loop(repo, ids):
    for item_id in ids:
        value = repo.get(item_id)
        if not value:
            continue
    await repo.flush()
    repo.update('all')
