# A guard whose body leaves the function from inside a with block still guards what follows.
async def refresh(repo, files, item_id):
    row = repo.get(item_id)
    if row:  # expect: check
        with files.open(item_id) as handle:
            return handle.read()
    await repo.sync(item_id)  # expect: gap await
    repo.update(item_id)  # expect: write
