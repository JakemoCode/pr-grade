// A guard that exits by continue covers the rest of its loop, not the code after the loop.
export async function drain(repo: any, ids: string[]) {
  for (const id of ids) {
    const value = repo.get(id);
    if (!value) continue; // expect: check
    await repo.sync(id); // expect: gap await
    repo.update(id); // expect: write
  }
}

export async function afterLoop(repo: any, ids: string[]) {
  for (const id of ids) {
    const value = repo.get(id);
    if (!value) continue;
  }
  await repo.flush();
  repo.update('all');
}
