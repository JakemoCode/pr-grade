# Collections built inside the function are not shared state.
async def gather(repo, ids):
    out = []
    seen = {}
    for item_id in ids:
        value = repo.get(item_id)
        if not value:
            continue
        await repo.touch(item_id)
        out.append(value)
        seen.update({item_id: True})
    return out
