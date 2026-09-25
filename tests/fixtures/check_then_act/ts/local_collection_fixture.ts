// Collections built inside the function are not shared state.
export async function gather(repo: any, ids: string[]) {
  const out: string[] = [];
  const seen = new Map<string, boolean>();
  for (const id of ids) {
    const value = repo.get(id);
    if (!value) continue;
    await repo.touch(id);
    out.push(value);
    seen.set(id, true);
  }
  return out;
}
