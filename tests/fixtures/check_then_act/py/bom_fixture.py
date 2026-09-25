# A byte order mark is not code.
async def settle(repo, order_id):
    order = repo.get(order_id)
    if order:  # expect: check
        return
    await repo.sync(order_id)  # expect: gap await
    repo.update(order_id)  # expect: write
