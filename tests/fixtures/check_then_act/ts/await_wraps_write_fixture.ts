// The await is the write itself, not something between the check and the write.
export async function store(repo: any, id: string) {
  const current = repo.get(id);
  if (current) return;
  await repo.insert(id);
}
