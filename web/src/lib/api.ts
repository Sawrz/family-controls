import { z } from 'zod';
import {
  account,
  history,
  policy,
  status,
  webConfig,
  type Child,
  type Command,
  type Action,
} from './contracts';
import type { Message } from './i18n';

export class ApiError extends Error {
  constructor(
    public status: number,
    public key: Message,
  ) {
    super(key);
  }
}

/** Same-origin cookie transport. Authentication tokens never enter browser storage. */
export class Api {
  async request<T>(
    path: string,
    schema: z.ZodType<T>,
    method = 'GET',
    body?: unknown,
    key?: string,
  ): Promise<T> {
    let response: Response;
    try {
      response = await fetch(path, {
        method,
        credentials: 'same-origin',
        cache: 'no-store',
        headers: {
          Accept: 'application/json',
          ...(body !== undefined ? { 'Content-Type': 'application/json' } : {}),
          ...(key ? { 'Idempotency-Key': key } : {}),
        },
        body: body === undefined ? undefined : JSON.stringify(body),
        signal: AbortSignal.timeout(15000),
      });
    } catch {
      throw new ApiError(0, 'network');
    }
    if (!response.ok) {
      const errors: Record<number, Message> = {
        400: 'invalid',
        401: 'authExpired',
        403: 'forbidden',
        404: 'invalid',
        409: 'conflict',
        422: 'invalid',
        429: 'rateLimit',
      };
      throw new ApiError(response.status, errors[response.status] ?? 'network');
    }
    try {
      return schema.parse(await response.json());
    } catch {
      throw new ApiError(502, 'unexpected');
    }
  }
  config() {
    return this.request('/web-config.json', webConfig);
  }
  me() {
    return this.request('/api/v1/me', account);
  }
  async children(): Promise<Child[]> {
    const { children } = await this.request(
      '/api/v1/children',
      z.object({ children: z.array(z.string()) }),
    );
    const result: Child[] = [];
    // Bound concurrent work on larger households and small self-hosted servers.
    for (let i = 0; i < children.length; i += 6) {
      result.push(
        ...(await Promise.all(
          children.slice(i, i + 6).map(async (id) => {
            const [p, s] = await Promise.all([
              this.request(this.path(id, 'policy'), policy),
              this.request(this.path(id, 'status'), status),
            ]);
            return { id, policy: p, status: s };
          }),
        )),
      );
    }
    return result;
  }
  path(child: string, suffix: string) {
    return `/api/v1/children/${encodeURIComponent(child)}/${suffix}`;
  }
  async history(children: Child[]) {
    const result = [];
    for (const child of children) {
      const batch = await this.request(this.path(child.id, 'history'), history);
      result.push(...batch.events.map((event) => ({ ...event, child: child.id })));
    }
    return result.sort((a, b) => b.at.localeCompare(a.at));
  }
}

export type Intent = { owner: string; child: string; action: Action; body: Command; key: string };
const intentSchema = z.object({
  owner: z.string(),
  child: z.string(),
  action: z.string(),
  body: z.record(z.string(), z.unknown()),
  key: z.string().uuid(),
});
/** Retains only an uncertain command, not credentials, so reloads cannot duplicate a grant. */
export function remember(intent: Intent | null): void {
  if (intent) sessionStorage.setItem('family.uncertain', JSON.stringify(intent));
  else sessionStorage.removeItem('family.uncertain');
}
export function recalled(owner: string): Intent | null {
  try {
    const parsed = intentSchema.parse(
      JSON.parse(sessionStorage.getItem('family.uncertain') ?? 'null'),
    );
    return parsed.owner === owner ? (parsed as Intent) : null;
  } catch {
    return null;
  }
}
export function errorKey(error: unknown): Message {
  return error instanceof ApiError ? error.key : 'unexpected';
}
