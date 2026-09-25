// A member flag checked after the await says nothing about the row read before it.
export class Worker {
  private closed = false;
  constructor(private readonly repo: any) {}

  async run(id: string) {
    const row = this.repo.get(id);
    if (row) return; // expect: check
    await this.repo.sync(id); // expect: gap await
    if (this.closed) return;
    this.repo.update(id); // expect: write
  }
}
