// Failure bookkeeping in a catch block is not the write the check guards.
export async function sync(repo: any, id: string) {
  if (!repo.has(id)) return;
  try {
    await repo.pull(id);
  } catch (error) {
    repo.recordError(id, error);
  }
}
