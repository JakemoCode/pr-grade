# The shape of a real race: a lookup, an awaited capture, then writes the lookup was meant to guard.
class Gate:
    def __init__(self, options):
        self.options = options

    async def evaluate(self, execution_id):
        execution = self.options.executions.get(execution_id)
        if execution is None:  # expect: check
            raise LookupError('missing')
        recorded = next((e for e in self.options.gates.for_execution(execution.run_id) if e.gate_id == 'g'), None)
        if recorded is not None:  # expect: check
            return recorded
        snapshot = await self.options.snapshots.capture(execution.run_id)  # expect: gap await
        self.options.runs.append_snapshot_advanced(run_id=execution.run_id, snapshot_id=snapshot.id)  # expect: write
        return self.options.gates.ensure_gate_evaluation(execution_id=execution.id)  # expect: write
