// The await is inside the check's own condition.
export async function create(repo: any, id: string) {
  if (await repo.exists(id)) return;
  repo.insert(id);
}
