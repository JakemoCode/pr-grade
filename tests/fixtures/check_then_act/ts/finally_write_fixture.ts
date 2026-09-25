// A finally block runs on the normal path too, so its write is the guarded write.
export async function finish(repo: any, id: string) {
  const row = repo.get(id);
  if (row) return; // expect: check
  try {
    await repo.sync(id); // expect: gap await
  } finally {
    repo.update(id); // expect: write
  }
}
