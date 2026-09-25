# A helper named like a write is a write even when it is not a method.
async def order(repo, order_id):
    row = repo.get(order_id)
    if row:  # expect: check
        return
    await repo.sync(order_id)  # expect: gap await
    save_order(order_id)  # expect: write


def save_order(order_id):
    pass
