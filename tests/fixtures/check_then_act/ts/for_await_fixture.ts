// Each turn of a for await waits, so a check before it goes stale.
export async function consume(repo: any, stream: AsyncIterable<string>, id: string) {
  const row = repo.get(id);
  if (row) return; // expect: check
  for await (const event of stream) { // expect: gap for-await
    repo.update(id, event); // expect: write
  }
}
