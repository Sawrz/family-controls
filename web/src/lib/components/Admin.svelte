<script lang="ts">
  import { onMount } from 'svelte';
  import { z } from 'zod';
  import { Api, errorKey } from '../api';
  import { account, device, type Account, type Device, type Child } from '../contracts';
  import { translate, type Language, type Message } from '../i18n';
  let { children, language, current }: { children: Child[]; language: Language; current: string } =
    $props();
  let accounts = $state<Account[]>([]),
    devices = $state<Device[]>([]),
    busy = $state(false);
  let error = $state<Message | null>(null),
    message = $state<Message | null>(null),
    token = $state('');
  let invited = $state(''),
    method = $state('password'),
    role = $state('parent');
  let deviceId = $state('');
  let linkAccount = $state(''),
    kind = $state('telegram'),
    external = $state(''),
    issuer = $state('');
  let revokeTarget = $state<{ kind: 'accounts' | 'devices'; id: string } | null>(null);
  const api = new Api(),
    t = (key: Message) => translate(language, key);
  const deviceNames = $derived([
    ...new Set(
      children
        .flatMap((c) => c.policy.assignments.map((a) => a.device))
        .filter(
          (name) => !devices.some((d) => d.device === name && d.managedBy === 'configuration'),
        ),
    ),
  ]);
  async function refresh() {
    try {
      const [a, d] = await Promise.all([
        api.request('/api/v1/accounts', z.object({ accounts: z.array(account) })),
        api.request('/api/v1/devices', z.object({ devices: z.array(device) })),
      ]);
      accounts = a.accounts;
      devices = d.devices;
      if (!deviceNames.includes(deviceId)) deviceId = deviceNames[0] ?? '';
    } catch (e) {
      error = errorKey(e);
    }
  }
  onMount(() => {
    void refresh();
    const timer = setInterval(() => {
      if (!document.hidden && !busy) void refresh();
    }, 15000);
    return () => clearInterval(timer);
  });
  async function send(path: string, body?: unknown, notice?: Message) {
    if (busy) return;
    busy = true;
    error = null;
    message = null;
    token = '';
    try {
      const result = await api.request(
        path,
        z.object({ token: z.string().optional() }).passthrough(),
        'POST',
        body,
      );
      token = result.token ?? '';
      message = notice ?? null;
      revokeTarget = null;
      await refresh();
    } catch (e) {
      error = errorKey(e);
    } finally {
      busy = false;
    }
  }
  async function link(event: SubmitEvent) {
    event.preventDefault();
    await send(
      `/api/v1/identities${kind === 'oidc' ? '/oidc' : ''}`,
      kind === 'oidc'
        ? { account: linkAccount, issuer, subject: external }
        : { account: linkAccount, interface: kind, externalId: external },
      'linked',
    );
  }
</script>

<section class="administration">
  <h2>{t('administrator')}</h2>
  {#if error}<p class="alert error" role="alert">{t(error)}</p>{/if}
  {#if message}<p class="alert" role="status">{t(message)}</p>{/if}
  {#if token}<div class="alert" role="status">
      <label>{t('token')}<input readonly value={token} autocomplete="off" /></label><button
        onclick={() => (token = '')}>{t('clear')}</button
      >
    </div>{/if}
  {#if revokeTarget}<div class="alert warning">
      <p>{t('confirmRevoke')} <strong>{revokeTarget.id}</strong>?</p>
      <div class="form-actions">
        <button
          class="danger"
          disabled={busy}
          onclick={() =>
            revokeTarget &&
            send(`/api/v1/${revokeTarget.kind}/${encodeURIComponent(revokeTarget.id)}/revoke`)}
          >{t('revoke')}</button
        ><button onclick={() => (revokeTarget = null)}>{t('cancel')}</button>
      </div>
    </div>{/if}
  <h3>{t('accounts')}</h3>
  <ul class="record-list">
    {#each accounts as item}<li>
        <div>
          <strong>{item.username}</strong>
          <p class="help">{t(item.role)} · {t(item.status)}</p>
        </div>
        <div class="form-actions">
          {#if item.status === 'pending'}<button
              disabled={busy}
              onclick={() =>
                send(`/api/v1/accounts/${encodeURIComponent(item.username)}/approve`, {
                  role: 'parent',
                })}>{t('approve')}</button
            >{/if}{#if item.status !== 'revoked' && item.username !== current}<button
              disabled={busy}
              onclick={() => (revokeTarget = { kind: 'accounts', id: item.username })}
              >{t('revoke')}</button
            >{/if}
        </div>
      </li>{/each}
  </ul>
  <h3>{t('devices')}</h3>
  <p class="help">{t('deviceHint')}</p>
  <ul class="record-list">
    {#each devices as item}<li>
        <div>
          <strong>{item.device}</strong>
          <p class="help">{t(item.status as Message)}</p>
          {#if item.managedBy === 'configuration'}<p class="help">
              {t('configurationManaged')}
            </p>{/if}
        </div>
        <div class="form-actions">
          {#if item.managedBy !== 'configuration' && item.status === 'pending'}<button
              disabled={busy}
              onclick={() => send(`/api/v1/devices/${encodeURIComponent(item.device)}/approve`)}
              >{t('approve')}</button
            >{/if}{#if item.managedBy !== 'configuration' && item.status !== 'revoked'}<button
              disabled={busy}
              onclick={() => (revokeTarget = { kind: 'devices', id: item.device })}
              >{t('revoke')}</button
            >{/if}
        </div>
      </li>{:else}<li>{t('noDevices')}</li>{/each}
  </ul>
  <div class="admin-forms">
    <form
      onsubmit={(event) => {
        event.preventDefault();
        void send('/api/v1/invitations', { identity: invited, method, role });
      }}
    >
      <h3>{t('invite')}</h3>
      <label>{t('identity')}<input bind:value={invited} required maxlength="128" /></label><label
        >{t('method')}<select aria-label={t('method')} bind:value={method}
          ><option value="password">{t('password')}</option><option value="oidc">OIDC</option
          ></select
        ></label
      ><label
        >{t('role')}<select aria-label={t('role')} bind:value={role}
          ><option value="parent">{t('parent')}</option><option value="admin">{t('admin')}</option
          ></select
        ></label
      ><button class="primary" disabled={busy}>{t('invite')}</button>
    </form>
    {#if deviceNames.length}<form
        onsubmit={(event) => {
          event.preventDefault();
          void send(`/api/v1/devices/${encodeURIComponent(deviceId)}/invitation`);
        }}
      >
        <h3>{t('enroll')}</h3>
        <label
          >{t('computers')}<select aria-label={t('computers')} bind:value={deviceId} required
            >{#each deviceNames as name}<option value={name}>{name}</option>{/each}</select
          ></label
        ><button class="primary" disabled={busy || !deviceId}>{t('enroll')}</button>
      </form>{/if}
    <form onsubmit={link}>
      <h3>{t('linkIdentity')}</h3>
      <label
        >{t('account')}<select aria-label={t('account')} bind:value={linkAccount} required
          ><option value="">—</option
          >{#each accounts.filter((a) => a.status === 'approved') as a}<option value={a.username}
              >{a.username}</option
            >{/each}</select
        ></label
      ><label
        >{t('method')}<select aria-label={t('method')} bind:value={kind}
          ><option value="telegram">Telegram</option><option value="matrix">Matrix</option><option
            value="oidc">OIDC</option
          ></select
        ></label
      >{#if kind === 'oidc'}<label
          >{t('issuer')}<input type="url" bind:value={issuer} required /></label
        >{/if}<label
        >{t(kind === 'oidc' ? 'subject' : 'externalId')}<input
          bind:value={external}
          required
          maxlength="128"
        /></label
      ><button class="primary" disabled={busy}>{t('link')}</button>
    </form>
  </div>
</section>
