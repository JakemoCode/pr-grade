// Awaits inside callbacks nobody awaits are not gaps for the function that schedules them.
export async function importAll(repo: any, items: string[], key: string) {
  const existing = repo.find(key);
  if (existing) return existing;
  items.forEach(async (item) => {
    await repo.warm(item);
  });
  setTimeout(async () => {
    await repo.flush();
  }, 10);
  repo.insert(key);
}
