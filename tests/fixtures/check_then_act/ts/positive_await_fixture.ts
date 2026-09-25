// The shape of a real race: a lookup, an awaited capture, then writes the lookup was meant to guard.
export class Gate {
  constructor(private readonly options: any) {}

  async evaluate(input: { executionId: string }) {
    const execution = this.options.executions.get(input.executionId);
    if (execution === undefined) throw new Error('missing'); // expect: check
    const recorded = this.options.gates.forExecution(execution.runId).find((e: any) => e.gateId === 'g');
    if (recorded !== undefined) return recorded; // expect: check
    const snapshot = await this.options.snapshots.capture(execution.runId); // expect: gap await
    this.options.runs.appendSnapshotAdvanced({ runId: execution.runId, snapshotId: snapshot.id }); // expect: write
    return this.options.gates.ensureGateEvaluation({ executionId: execution.id }); // expect: write
  }
}
