// A later guard on an unrelated flag of the same object does not re-read what the first check read.
export class Service {
  private paused = false;
  constructor(private readonly store: any) {}

  async apply(id: string) {
    const item = this.store.get(id);
    if (item.status !== 'pending') return item; // expect: check
    await this.slowStep(id); // expect: gap await
    if (this.isPaused()) return undefined;
    this.store.markApplied(id); // expect: write
  }

  private isPaused() {
    return this.paused;
  }

  private async slowStep(id: string) {
    return Promise.resolve(id);
  }
}
