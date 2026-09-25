// A later check on a different store does not revalidate the first one.
export async function apply(store: any, other: any, id: string) {
  const record = store.get(id);
  if (record.active) { // expect: check
    await slowCheck(id); // expect: gap await
    const cfg = other.getConfig();
    if (cfg.enabled) {
      store.update(id); // expect: write
    }
  }
}

declare function slowCheck(id: string): Promise<void>;
