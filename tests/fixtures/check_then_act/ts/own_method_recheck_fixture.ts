// The state is read again after the await through the object's own helper, which may read any store.
export class Service {
  constructor(private readonly repo: any, private readonly graph: any) {}

  async apply(id: string) {
    const item = this.repo.get(id);
    if (item.status !== 'pending') return item;
    await this.graph.project(id);
    const after = this.stored(id);
    if (after.status !== 'pending') return after;
    this.repo.markApplied(id);
  }

  private stored(id: string) {
    return this.repo.get(id);
  }
}
