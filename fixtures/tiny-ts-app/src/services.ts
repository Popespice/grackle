import { User } from "./models";
import { Status, type UserRole } from "./types";
import { capitalize, generateId } from "./utils";

export class UserService {
  private users: User[] = [];

  createUser(name: string, role: UserRole): User {
    const id = generateId();
    const user = new User(id, capitalize(name), role);
    this.users.push(user);
    return user;
  }

  getActiveUsers(): User[] {
    return this.users.filter((u) => u.getStatus() === Status.Active);
  }

  printAll(): void {
    for (const u of this.users) {
      u.print();
    }
  }
}

export function createAdminUser(name: string): User {
  const service = new UserService();
  return service.createUser(name, "admin" as UserRole);
}
