import type { Config } from "./types";

export class ApiClient {
  fetch(path: string): Promise<unknown> {
    return fetch(path);
  }
}

export function createClient(config: Config): ApiClient {
  return new ApiClient(config);
}
