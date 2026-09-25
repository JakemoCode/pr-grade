# A form feed is not a line break to Python, so line text must still match the line.

async def settle(repo, order_id):
    order = repo.get(order_id)
    if order:  # expect: check
        return
    await repo.sync(order_id)  # expect: gap await
    repo.update(order_id)  # expect: write
