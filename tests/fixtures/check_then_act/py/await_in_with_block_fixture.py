# An await inside a with block still sits between a check before it and a write after it.
async def move(repo, files, item_id):
    row = repo.get(item_id)
    if row:  # expect: check
        return
    with files.open(item_id) as handle:
        await handle.flush()  # expect: gap await
    repo.update(item_id)  # expect: write
