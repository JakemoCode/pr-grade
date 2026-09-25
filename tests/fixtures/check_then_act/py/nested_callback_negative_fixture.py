# Work handed to a task nobody awaits, or to a lambda, is not a gap for the function that schedules it.
import asyncio


async def import_all(repo, items, key):
    existing = repo.find(key)
    if existing:
        return existing
    for item in items:
        asyncio.create_task(repo.warm(item))
    asyncio.get_running_loop().call_later(10, lambda: repo.flush())
    repo.insert(key)
