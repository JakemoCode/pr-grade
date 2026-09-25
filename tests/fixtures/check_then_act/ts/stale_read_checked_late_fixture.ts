// The read happens before the await and the check after it: the check tests stale data.
export async function settle(repo: any, id: string) {
  const order = repo.get(id);
  await repo.sync(id); // expect: gap await
  if (order.open) { // expect: check
    repo.update(id, { open: false }); // expect: write
  }
}
