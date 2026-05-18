export interface Serializable {
  serialize(): string;
}

export interface Printable {
  print(): void;
}

export type UserId = string;
export type UserRole = "admin" | "viewer";

export enum Status {
  Active = "ACTIVE",
  Inactive = "INACTIVE",
}
