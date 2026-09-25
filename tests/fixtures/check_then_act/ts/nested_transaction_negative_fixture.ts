// A transaction nested inside the one holding the check is a savepoint: nothing lands between them.
export function build(db: any, repo: any, key: string) {
  return db.transaction(() => {
    if (repo.find(key)) return undefined;
    return db.transaction(() => repo.insert(key));
  });
}
