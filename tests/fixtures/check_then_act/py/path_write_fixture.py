# In Python a name like path usually holds a Path, and its writes reach the filesystem.
async def save(path, data):
    if path.exists():  # expect: check
        return
    await render(data)  # expect: gap await
    path.write_text(data)  # expect: write


async def render(data):
    pass
