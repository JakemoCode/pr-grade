// A try block that returns still runs its finally, so a write there comes after the await.
export async function finish(repo: any, id: string) {
  const row = repo.get(id);
  if (row) return; // expect: check
  try {
    await repo.sync(id); // expect: gap await
    return;
  } finally {
    repo.update(id); // expect: write
  }
}
