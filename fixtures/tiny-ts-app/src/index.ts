import { createAdminUser, UserService } from "./services";
import type { UserId } from "./types";

export { UserService };

export function bootstrap(): void {
  const service = new UserService();
  createAdminUser("Admin");
  service.printAll();
}

export type { UserId };
