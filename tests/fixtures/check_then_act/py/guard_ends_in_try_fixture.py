# A guard whose try and every handler leave the function still guards what follows.
async def refresh(repo, item_id):
    row = repo.get(item_id)
    if row:  # expect: check
        try:
            return row.value
        except AttributeError:
            return None
    await repo.sync(item_id)  # expect: gap await
    repo.update(item_id)  # expect: write
