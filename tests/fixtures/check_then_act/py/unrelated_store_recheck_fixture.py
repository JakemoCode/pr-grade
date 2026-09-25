# A later check on a different store does not revalidate the first one.
async def apply(store, other, item_id):
    record = store.get(item_id)
    if record.active:  # expect: check
        await slow_check(item_id)  # expect: gap await
        cfg = other.get_config()
        if cfg.enabled:
            store.update(item_id)  # expect: write


async def slow_check(item_id):
    pass
