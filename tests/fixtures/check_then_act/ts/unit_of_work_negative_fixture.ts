// The check and the write share one synchronous unit of work, so nothing can land between them.
export class Runtime {
  constructor(private readonly db: any, private readonly store: any) {}

  recordReturn(input: { id: string }) {
    return this.db.unitOfWork(() => {
      const execution = this.store.require(input.id);
      if (execution.status !== 'running') return { status: 'rejected' };
      this.store.append({ id: input.id, kind: 'returned' });
      return { status: 'accepted' };
    });
  }
}
