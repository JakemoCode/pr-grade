// The state is read and checked again after the await, so the first check is not what the write relies on.
export async function apply(repo: any, id: string) {
  const first = repo.get(id);
  if (!first) return;
  await repo.refresh(id);
  const current = repo.get(id);
  if (!current || current.done) return;
  repo.update(id, { done: true });
}
