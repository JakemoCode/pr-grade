// A check outside the unit of work guards a write inside it; the second method re-checks inside.
export class Builder {
  constructor(private readonly db: any, private readonly repo: any) {}

  build(key: string) {
    const found = this.repo.find(key);
    if (found) return found; // expect: check
    return this.db.unitOfWork(() => { // expect: gap transaction
      return this.repo.insert(key); // expect: write
    });
  }

  buildRechecked(key: string) {
    const found = this.repo.find(key);
    if (found) return found;
    return this.db.unitOfWork(() => {
      if (this.repo.find(key)) return undefined;
      return this.repo.insert(key);
    });
  }
}
