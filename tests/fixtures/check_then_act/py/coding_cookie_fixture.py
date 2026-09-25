# -*- coding: latin-1 -*-
# A declared encoding decides how the bytes read: café.
async def settle(repo, order_id):
    order = repo.get(order_id)
    if order:  # expect: check
        return
    await repo.sync(order_id)  # expect: gap await
    repo.update(order_id)  # expect: write
