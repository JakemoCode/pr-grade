// A helper named like a write is a write even when it is not a method.
export async function order(repo: any, id: string) {
  const row = repo.get(id);
  if (row) return; // expect: check
  await repo.sync(id); // expect: gap await
  saveOrder(id); // expect: write
}

declare function saveOrder(id: string): void;
