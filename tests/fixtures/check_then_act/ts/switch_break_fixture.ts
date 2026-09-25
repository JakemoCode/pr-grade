// An await in a switch case that breaks still reaches the code after the switch.
export async function refresh(repo: any, id: string, k: string) {
  const row = repo.get(id);
  if (row) return; // expect: check
  switch (k) {
    case 'a': {
      await repo.sync(id); // expect: gap await
      break;
    }
  }
  repo.update(id); // expect: write
}
