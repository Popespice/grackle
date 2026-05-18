import {
  type Printable,
  type Serializable,
  Status,
  type UserId,
  type UserRole,
} from "./types";

export class BaseEntity {
  constructor(readonly id: UserId) {}

  serialize(): string {
    return JSON.stringify({ id: this.id });
  }
}

export class User extends BaseEntity implements Serializable, Printable {
  constructor(
    id: UserId,
    readonly name: string,
    readonly role: UserRole
  ) {
    super(id);
  }

  serialize(): string {
    return JSON.stringify({ id: this.id, name: this.name });
  }

  print(): void {
    console.log(this.serialize());
  }

  getStatus(): Status {
    return Status.Active;
  }
}
