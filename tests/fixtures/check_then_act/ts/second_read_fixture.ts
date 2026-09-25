// A synchronous call to another system between the check and the write; flagged only when configured.
export function charge(repo: any, billing: any, id: string) {
  const invoice = repo.get(id);
  if (invoice.paid) return; // expect: check
  billing.callBilling(id); // expect: gap second-read
  repo.markPaid(id); // expect: write
}
