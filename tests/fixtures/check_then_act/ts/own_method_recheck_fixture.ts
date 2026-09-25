// A recheck through the object's own method cannot be told from one that reads an unrelated flag, so
// the window stays listed for the grader to clear.
export class Service {
  constructor(private readonly repo: any, private readonly graph: any) {}

  async apply(id: string) {
    const item = this.repo.get(id);
    if (item.status !== 'pending') return item; // expect: check
    await this.graph.project(id); // expect: gap await
    const after = this.stored(id);
    if (after.status !== 'pending') return after;
    this.repo.markApplied(id); // expect: write
  }

  private stored(id: string) {
    return this.repo.get(id);
  }
}
