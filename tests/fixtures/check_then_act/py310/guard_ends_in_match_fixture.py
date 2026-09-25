# A guard whose match leaves the function in every case, the last one catching all, guards what follows.
async def refresh(repo, item_id):
    row = repo.get(item_id)
    if row:  # expect: check
        match row.kind:
            case 'a':
                return 1
            case _:
                return 2
    await repo.sync(item_id)  # expect: gap await
    repo.update(item_id)  # expect: write


async def partial(repo, item_id):
    row = repo.get(item_id)
    if row:
        match row.kind:
            case 'a':
                return 1
    repo.update(item_id)
