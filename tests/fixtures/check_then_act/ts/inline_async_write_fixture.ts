// The await and the write share an async callback, and the check outside guards them both.
export function fanout(store: any, id: string, items: string[]) {
  const record = store.get(id);
  if (record.active) { // expect: check
    items.forEach(async (item) => {
      await slowCheck(item); // expect: gap await
      store.update(id, item); // expect: write
    });
  }
}

declare function slowCheck(item: string): Promise<void>;
