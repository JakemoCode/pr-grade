// A counter checked before the await and raised after it bounds nothing. An unrelated member check
// in save() must not pair with a store write.
export class Limiter {
  private inflight = 0;
  private closed = false;
  constructor(private readonly max: number, private readonly store: any) {}

  async run(task: () => Promise<void>) {
    if (this.inflight >= this.max) return; // expect: check
    await task(); // expect: gap await
    this.inflight++; // expect: write
  }

  async save(value: string) {
    if (this.closed) return;
    await this.store.prepare(value);
    this.store.append(value);
  }
}
