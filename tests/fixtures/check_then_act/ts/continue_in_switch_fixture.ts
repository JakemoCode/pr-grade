// continue skips the switch around it and leaves the loop iteration, so the guard reaches past the switch.
export async function process(repo: any, ids: string[]) {
  for (const id of ids) {
    switch (id) {
      case 'x': {
        if (!repo.get(id)) continue; // expect: check
        break;
      }
    }
    await repo.sync(id); // expect: gap await
    repo.update(id); // expect: write
  }
}
