# The read happens before the await and the check after it: the check tests stale data.
async def settle(repo, order_id):
    order = repo.get(order_id)
    await repo.sync(order_id)  # expect: gap await
    if order.open:  # expect: check
        repo.update(order_id, open=False)  # expect: write
