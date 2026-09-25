// A labeled continue leaves the loop its label names, not the nearest one.
export async function sweep(repo: any, groups: string[][]) {
  outer: for (const ids of groups) {
    for (const id of ids) {
      if (!repo.get(id)) continue outer; // expect: check
    }
    await repo.sync(ids); // expect: gap await
    repo.update(ids); // expect: write
  }
}
