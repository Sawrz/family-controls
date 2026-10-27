<script lang="ts">
  import { childName } from '../contracts';
  import { onMount } from 'svelte';
  import { z } from 'zod';
  import { Api, errorKey } from '../api';
  import {
    account,
    authentication,
    device,
    assignmentLabel,
    type Account,
    type Device,
    type Child,
  } from '../contracts';
  import { translate, dateTime, type Language, type Message } from '../i18n';
  import Integrations from './Integrations.svelte';
  import ChildSetup from './ChildSetup.svelte';
  let {
    children,
    language,
    current,
    onchanged,
  }: { children: Child[]; language: Language; current: string; onchanged: () => Promise<void> } =
    $props();
  type Section = 'people' | 'childrenAdmin' | 'devices' | 'integrations' | 'signInMethods';
  const sections: Section[] = [
    'people',
    'childrenAdmin',
    'devices',
    'integrations',
    'signInMethods',
  ];
  let section = $state<Section>('people');
  let accounts = $state<Account[]>([]),
    devices = $state<Device[]>([]),
    auth = $state<z.infer<typeof authentication> | null>(null);
  let busy = $state(false),
    loading = $state(true),
    error = $state<Message | null>(null),
    notice = $state<Message | null>(null),
    token = $state('');
  let invited = $state(''),
    oidcInvited = $state(''),
    inviteRole = $state('parent'),
    oidcRole = $state('parent');
  let deviceId = $state(''),
    setupId = $state('');
  let linkAccounts = $state<Record<string, string>>({}),
    subjects = $state<Record<string, string>>({});
  let confirmation = $state<{
    path: string;
    label: string;
    method: string;
    body?: unknown;
    message: Message;
  } | null>(null);
  const api = new Api(),
    t = (key: Message) => translate(language, key);
  const manualDevices = $derived([
    ...new Set(
      children
        .flatMap((c) => c.policy.assignments.map((a) => a.device))
        .filter(
          (name) => !devices.some((d) => d.device === name && d.managedBy === 'configuration'),
        ),
    ),
  ]);
  const settingUp = $derived(children.find((c) => c.id === setupId && !c.policy.setupComplete));
  async function refresh() {
    try {
      const [a, d, methods] = await Promise.all([
        api.request('/api/v1/accounts', z.object({ accounts: z.array(account) })),
        api.request('/api/v1/devices', z.object({ devices: z.array(device) })),
        api.request('/api/v1/authentication', authentication),
      ]);
      accounts = a.accounts;
      devices = d.devices;
      auth = methods;
      if (!manualDevices.includes(deviceId)) deviceId = manualDevices[0] ?? '';
      error = null;
    } catch (e) {
      error = errorKey(e);
    } finally {
      loading = false;
    }
  }
  onMount(() => {
    const requested = new URL(location.href).searchParams.get('section');
    if (sections.includes(requested as Section)) section = requested as Section;
    void refresh();
    const timer = setInterval(() => {
      if (!document.hidden && !busy) void refresh();
    }, 15000);
    return () => clearInterval(timer);
  });
  async function send(path: string, body?: unknown, message: Message = 'saved', method = 'POST') {
    if (busy) return;
    busy = true;
    error = null;
    notice = null;
    token = '';
    try {
      const result = await api.request(
        path,
        z.object({ token: z.string().optional() }).passthrough(),
        method,
        body,
      );
      token = result.token ?? '';
      notice = message;
      confirmation = null;
      if (path === `/api/v1/accounts/${encodeURIComponent(current)}/role`) await onchanged();
      await refresh();
    } catch (e) {
      error = errorKey(e);
    } finally {
      busy = false;
    }
  }
  function select(next: Section) {
    section = next;
    history.replaceState(null, '', `/?view=household&section=${next}`);
    token = '';
    notice = null;
    confirmation = null;
  }
</script>

<p>{t('householdIntro')}</p>
<nav class="section-nav" aria-label={t('householdSections')}>
  {#each sections as item}<button
      class:active={section === item}
      aria-current={section === item ? 'page' : undefined}
      onclick={() => select(item)}>{t(item)}</button
    >{/each}
</nav>
{#if error}<p class="alert error" role="alert">
    {t(error)} <button onclick={refresh}>{t('retry')}</button>
  </p>{/if}
{#if notice}<p class="alert" role="status">{t(notice)}</p>{/if}
{#if loading}<p role="status">{t('loading')}</p>{/if}
{#if token}<div class="alert" role="status">
    <label>{t('token')}<input readonly value={token} autocomplete="off" /></label><button
      onclick={() => (token = '')}>{t('clear')}</button
    >
  </div>{/if}
{#if confirmation}<div class="alert warning">
    <p>{t(confirmation.message)} <strong>{confirmation.label}</strong>?</p>
    <div class="form-actions">
      <button
        class="danger"
        disabled={busy}
        onclick={() =>
          confirmation && send(confirmation.path, confirmation.body, 'saved', confirmation.method)}
        >{t('confirmAction')}</button
      ><button onclick={() => (confirmation = null)}>{t('cancel')}</button>
    </div>
  </div>{/if}
{#if section === 'people'}
  <section class="settings-section">
    <h2>{t('people')}</h2>
    <ul class="record-list">
      {#each accounts as item}<li>
          <div>
            <strong>{item.displayName}</strong>{#if item.displayName !== item.username}<p
                class="help"
              >
                {item.username}
              </p>{/if}
            <p class="help">{t(item.role)} · {t(item.status)}</p>
          </div>
          <div class="form-actions">
            {#if item.status === 'pending'}<button
                disabled={busy}
                onclick={() =>
                  send(`/api/v1/accounts/${encodeURIComponent(item.username)}/approve`, {
                    role: 'parent',
                  })}>{t('approve')}</button
              >{/if}
            {#if item.status === 'approved'}<button
                disabled={busy}
                onclick={() =>
                  (confirmation = {
                    path: `/api/v1/accounts/${encodeURIComponent(item.username)}/role`,
                    label: item.displayName,
                    method: 'PUT',
                    body: { role: item.role === 'admin' ? 'parent' : 'admin' },
                    message: item.role === 'admin' ? 'makeParent' : 'makeAdmin',
                  })}>{t(item.role === 'admin' ? 'makeParent' : 'makeAdmin')}</button
              >{/if}
            {#if item.status !== 'revoked' && item.username !== current}<button
                disabled={busy}
                onclick={() =>
                  (confirmation = {
                    path: `/api/v1/accounts/${encodeURIComponent(item.username)}/revoke`,
                    label: item.displayName,
                    method: 'POST',
                    message: 'confirmRevoke',
                  })}>{t('revoke')}</button
              >{/if}
          </div>
        </li>{/each}
    </ul>
  </section>
  {#if auth?.password.registration}<form
      class="settings-section settings-form"
      onsubmit={(event) => {
        event.preventDefault();
        void send('/api/v1/invitations', {
          identity: invited,
          method: 'password',
          role: inviteRole,
        });
      }}
    >
      <h3>{t('invitePassword')}</h3>
      <label>{t('username')}<input bind:value={invited} required maxlength="64" /></label><label
        >{t('role')}<select aria-label={t('role')} bind:value={inviteRole}
          ><option value="parent">{t('parent')}</option><option value="admin">{t('admin')}</option
          ></select
        ></label
      ><button disabled={busy}>{t('invite')}</button>
    </form>{/if}
  {#if auth?.oidc.registration}<form
      class="settings-section settings-form"
      onsubmit={(event) => {
        event.preventDefault();
        void send('/api/v1/invitations', { identity: oidcInvited, method: 'oidc', role: oidcRole });
      }}
    >
      <h3>{t('inviteOidc')}</h3>
      <p class="help">{t('oidcInvitationHint')}</p>
      <label>{t('identity')}<input bind:value={oidcInvited} required maxlength="128" /></label
      ><label
        >{t('role')}<select aria-label={t('role')} bind:value={oidcRole}
          ><option value="parent">{t('parent')}</option><option value="admin">{t('admin')}</option
          ></select
        ></label
      ><button disabled={busy}>{t('invite')}</button>
    </form>{/if}
{:else if section === 'childrenAdmin'}
  <h2>{t('childrenAdmin')}</h2>
  <p>{t('deviceHint')}</p>
  {#if settingUp}{#key settingUp.id}<ChildSetup
        child={settingUp}
        {children}
        {language}
        {onchanged}
      />{/key}{/if}
  <ul class="record-list">
    {#each children as child}<li>
        <div>
          <strong>{childName(child)}</strong>
          <p>{assignmentLabel(child) || t('noDevices')}</p>
          <p class="help">
            {child.policy.timezone} · {t(child.policy.setupComplete ? 'setupDone' : 'needsSetup')}
          </p>
        </div>
        {#if !child.policy.setupComplete}<button onclick={() => (setupId = child.id)}
            >{t('setupChild')}</button
          >{/if}
      </li>{/each}
  </ul>
{:else if section === 'devices'}
  <h2>{t('devices')}</h2>
  <p>{t('deviceHint')}</p>
  <ul class="record-list">
    {#each devices as item}<li>
        <div>
          <strong>{item.device}</strong>
          <p>{t(item.status as Message)}</p>
          <p class="help">
            {t('lastSeen')}: {item.lastSeen ? dateTime(language, item.lastSeen) : t('neverSeen')}
          </p>
          {#if item.managedBy === 'configuration'}<p class="help">
              {t('configurationManaged')}
            </p>{/if}
          {#each children.flatMap((c) => c.status.devices
              .filter((d) => d.device === item.device)
              .map((d) => ({ child: c, report: d }))) as entry}<p class="help">
              {childName(entry.child)}: {t(entry.report.connectivity as Message)} · {t(
                entry.report.policyRevision === entry.child.policy.revision ? 'applied' : 'pending',
              )}
            </p>{/each}
        </div>
        <div class="form-actions">
          {#if item.managedBy !== 'configuration'}{#if item.status === 'pending'}<button
                disabled={busy}
                onclick={() => send(`/api/v1/devices/${encodeURIComponent(item.device)}/approve`)}
                >{t('approve')}</button
              >{/if}{#if item.status !== 'revoked'}<button
                disabled={busy}
                onclick={() =>
                  (confirmation = {
                    path: `/api/v1/devices/${encodeURIComponent(item.device)}/revoke`,
                    label: item.device,
                    method: 'POST',
                    message: 'confirmRevoke',
                  })}>{t('revoke')}</button
              >{/if}{/if}
        </div>
      </li>{:else}<li>{t('noDevices')}</li>{/each}
  </ul>
  {#if manualDevices.length}<form
      class="settings-form"
      onsubmit={(event) => {
        event.preventDefault();
        void send(`/api/v1/devices/${encodeURIComponent(deviceId)}/invitation`);
      }}
    >
      <h3>{t('enroll')}</h3>
      <label
        >{t('computers')}<select aria-label={t('computers')} bind:value={deviceId} required
          >{#each manualDevices as name}<option value={name}>{name}</option>{/each}</select
        ></label
      ><button disabled={busy}>{t('enroll')}</button>
    </form>{/if}
{:else if section === 'integrations'}
  <h2>{t('integrations')}</h2>
  <Integrations {language} admin />
{:else}
  <h2>{t('signInMethods')}</h2>
  <p>{t('authenticationManaged')}</p>
  {#if auth}<section class="settings-section">
      <h3>{t('password')}</h3>
      <p>{t('login')}: {t(auth.password.login ? 'enabled' : 'disabled')}</p>
      <p>{t('registration')}: {t(auth.password.registration ? 'enabled' : 'disabled')}</p>
    </section>
    {#each auth.providers as provider}<section class="settings-section">
        <h3>{provider.label}</h3>
        <p class="help">{provider.issuer}</p>
        <p>{t('login')}: {t(auth.oidc.login ? 'enabled' : 'disabled')}</p>
        <p>{t('registration')}: {t(auth.oidc.registration ? 'enabled' : 'disabled')}</p>
        <p>{t('approvalRequired')}: {t(auth.requireApproval ? 'enabled' : 'disabled')}</p>
        <ul class="record-list">
          {#each accounts.filter((a) => a.oidc?.issuer === provider.issuer) as item}<li>
              <div>
                <strong>{item.displayName}</strong>
                <p class="help">{item.oidc?.subject}</p>
              </div>
            </li>{:else}<li>{t('noLinkedParents')}</li>{/each}
        </ul>
        <form
          class="settings-form"
          onsubmit={(event) => {
            event.preventDefault();
            void send(
              '/api/v1/identities/oidc',
              {
                account: linkAccounts[provider.id],
                issuer: provider.issuer,
                subject: subjects[provider.id],
              },
              'linked',
            );
          }}
        >
          <h4>{t('linkOidc')}</h4>
          <label
            >{t('account')}<select
              aria-label={t('account')}
              required
              bind:value={linkAccounts[provider.id]}
              ><option value="">—</option
              >{#each accounts.filter((a) => a.status === 'approved') as item}<option
                  value={item.username}>{item.displayName} ({item.username})</option
                >{/each}</select
            ></label
          ><label
            >{t('subject')}<input
              bind:value={subjects[provider.id]}
              required
              maxlength="128"
            /></label
          ><button disabled={busy}>{t('link')}</button>
        </form>
      </section>{/each}
  {/if}
{/if}
