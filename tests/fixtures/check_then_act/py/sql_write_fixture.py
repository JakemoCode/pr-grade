# SQL text names the write when the method does not. An awaited write is a gap for the writes after it.
async def close(conn, order_id):
    row = await conn.fetchrow('SELECT open FROM orders WHERE id = $1', order_id)
    if not row['open']:  # expect: check
        return
    await conn.execute('UPDATE ledger SET touched = now()')  # expect: gap await
    await notify(order_id)  # expect: gap await
    conn.execute('UPDATE orders SET open = false WHERE id = $1', order_id).close()  # expect: write


async def notify(order_id):
    pass
