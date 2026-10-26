import { z } from 'zod';

const method = z.object({ login: z.boolean(), registration: z.boolean() });
export const webConfig = z.object({
  password: method,
  oidc: method,
  providers: z.array(z.object({ id: z.string().regex(/^[a-zA-Z0-9_.-]+$/), label: z.string() })),
});
export type WebConfig = z.infer<typeof webConfig>;
export const account = z.object({
  username: z.string(),
  role: z.enum(['admin', 'parent']),
  status: z.enum(['pending', 'approved', 'revoked']),
  oidc: z.unknown(),
});
export type Account = z.infer<typeof account>;
const pendingRequest = z
  .object({
    id: z.string(),
    minutes: z.number().nullable(),
    reason: z.string(),
    requestedAt: z.string(),
    expires_at: z.string(),
  })
  .passthrough();
export const status = z.object({
  user: z.string(),
  phase: z.string(),
  revision: z.number().optional(),
  connectivity: z.string(),
  lastSeen: z.string().nullable(),
  application: z.object({ status: z.string(), error: z.string().nullable().optional() }),
  blocked: z.boolean().optional(),
  muted: z.boolean().optional(),
  insideUnmeteredWindow: z.boolean().nullable().optional(),
  pendingRequest: pendingRequest.nullable().optional(),
  remainingPlayTimeSeconds: z.number().nullable().optional(),
  remainingCountdownSeconds: z.number().nullable().optional(),
  effectiveDailyLimitMinutes: z.number().nullable(),
  allowedUntil: z.string().nullable().optional(),
  unlimitedToday: z.boolean().optional(),
  overrideExpiresAt: z.string().optional(),
  delivery: z.object({ pending: z.number(), status: z.string() }),
  policyDelivery: z.string(),
});
export type Status = z.infer<typeof status>;
export const policy = z.object({
  revision: z.number(),
  device: z.string(),
  user: z.string(),
  timezone: z.string(),
  policy: z.object({
    timeWindows: z.object({
      allowed: z.record(z.string(), z.array(z.string())),
      unmetered: z.record(z.string(), z.array(z.string())),
    }),
    playTime: z.object({ dailyLimitMinutes: z.number(), activities: z.array(z.string()) }),
    dnsProfiles: z.object({
      restrictedProfile: z.string(),
      homeworkProfile: z.string(),
      leisureProfile: z.string(),
    }),
    trackInactive: z.boolean(),
    countLockedTime: z.boolean(),
    blockTTYLogin: z.boolean(),
    lockoutAction: z.enum(['terminate', 'suspend', 'shutdown', 'lock', 'kill']),
    killSessions: z.boolean(),
  }),
});
export type Policy = z.infer<typeof policy>;
export type Child = { id: string; policy: Policy; status: Status };
export const history = z.object({
  events: z.array(
    z.object({
      at: z.string(),
      action: z.string(),
      outcome: z.string(),
      error: z.string().nullable(),
      details: z.object({ actor: z.string(), operationId: z.string() }),
    }),
  ),
});
export type HistoryEvent = z.infer<typeof history>['events'][number] & { child: string };
export const device = z.object({
  device: z.string(),
  status: z.string(),
  enrolledAt: z.string(),
  policyRevision: z.number(),
});
export type Device = z.infer<typeof device>;
export const actions = [
  'add-time',
  'deduct-time',
  'set-limit',
  'set-remaining',
  'grant-session',
  'extend-access',
  'reduce-access',
  'allow-until',
  'unlimited-today',
  'allow-today',
  'restore-today',
  'block',
  'unblock',
  'mute',
  'unmute',
] as const;
export type Action = (typeof actions)[number] | 'decide-request';
export type Command = {
  minutes?: number;
  until?: string;
  today?: boolean;
  days?: number;
  requestId?: string;
  decision?: string;
  reason?: string;
  expectedRevision?: number;
};
export const receipt = z.object({
  operation: z.object({ operationId: z.string(), status: z.string() }),
});
