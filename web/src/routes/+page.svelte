<script lang="ts">
  import { onMount, tick } from 'svelte';
  import { z } from 'zod';
  import Auth from '#lib/components/Auth.svelte';
  import CommandEditor from '#lib/components/CommandEditor.svelte';
  import PolicyEditor from '#lib/components/PolicyEditor.svelte';
  import Admin from '#lib/components/Admin.svelte';
  import { Api, ApiError, errorKey, recalled, remember, type Intent } from '#lib/api.ts';
  import {
    receipt,
    type Account,
    type WebConfig,
    type Child,
    type Action,
    type Command,
    type HistoryEvent,
  } from '#lib/contracts.ts';
  import { translate, actionLabel, dateTime, type Language, type Message } from '#lib/i18n.ts';

  type View = 'today' | 'requests' | 'schedules' | 'history' | 'settings';
  const views: View[] = ['today', 'requests', 'schedules', 'history', 'settings'];
  const api = new Api();
  let language = $state<Language>('en'),
    config = $state<WebConfig | null>(null),
    user = $state<Account | null>(null);
  let children = $state<Child[]>([]),
    events = $state<HistoryEvent[]>([]),
    view = $state<View>('today');
  let loading = $state(true),
    error = $state<Message | null>(null),
    transportDown = $state(false),
    live = $state(false);
  let selectedId = $state(''),
    requestMode = $state(false),
    search = $state(''),
    computer = $state('');
  let busy = $state(false),
    uncertain = $state<Intent | null>(null),
    notice = $state<Message | null>(null);
  let stream: EventSource | null = null,
    timer: ReturnType<typeof setInterval> | null = null;
  let refreshing = false,
    generation = 0,
    stopped = false;
  const t = (key: Message) => translate(language, key);
  const selected = $derived(children.find((c) => c.id === selectedId));
  const pending = $derived(
    children.filter(
      (c) =>
        c.status.pendingRequest &&
        new Date(c.status.pendingRequest.expires_at).getTime() > Date.now(),
    ),
  );
  const deviceNames = $derived([...new Set(children.map((c) => c.policy.device))]);
  const visible = $derived(
    children.filter(
      (c) =>
        (!computer || c.policy.device === computer) &&
        `${c.id} ${c.policy.device} ${c.policy.user}`
          .toLocaleLowerCase()
          .includes(search.toLocaleLowerCase()),
    ),
  );
  function stopLive() {
    stream?.close();
    stream = null;
    if (timer) clearInterval(timer);
    timer = null;
    live = false;
  }
  function expire() {
    generation++;
    stopLive();
    user = null;
    children = [];
    events = [];
    selectedId = '';
    error = 'authExpired';
  }
  async function refresh() {
    if (refreshing || !user || stopped) return;
    refreshing = true;
    const current = generation;
    try {
      const result = await api.children();
      const latestEvents = view === 'history' ? await api.history(result) : null;
      if (generation !== current || stopped) return;
      children = result;
      transportDown = false;
      if (latestEvents) events = latestEvents;
    } catch (e) {
      if (generation !== current || stopped) return;
      if (e instanceof ApiError && [401, 403].includes(e.status)) expire();
      else transportDown = true;
    } finally {
      refreshing = false;
    }
  }
  function startLive() {
    stopLive();
    stream = new EventSource('/api/v1/events');
    stream.onopen = () => (live = true);
    stream.onerror = () => (live = false);
    stream.addEventListener('change', () => {
      void refresh();
    });
    // Device usage changes do not produce an event per tick. Refresh snapshots too.
    timer = setInterval(() => {
      if (!document.hidden) void refresh();
    }, 10000);
  }
  async function login() {
    user = await api.me();
    generation++;
    error = null;
    notice = null;
    uncertain = recalled(user.username);
    await refresh();
    if (user && !stopped) startLive();
  }
  async function initialize() {
    loading = true;
    error = null;
    try {
      config = await api.config();
    } catch {
      error = 'configError';
      loading = false;
      return;
    }
    try {
      await login();
    } catch (e) {
      if (!(e instanceof ApiError && e.status === 401)) error = errorKey(e);
    } finally {
      loading = false;
    }
  }
  async function logout() {
    busy = true;
    try {
      await api.request('/api/v1/auth/logout', z.object({ status: z.string() }), 'POST');
      expire();
      error = null;
      notice = null;
      uncertain = null;
    } catch (e) {
      error = errorKey(e);
    } finally {
      busy = false;
    }
  }
  function setLanguage() {
    document.documentElement.lang = language;
    try {
      localStorage.setItem('family.language', language);
    } catch {
      /* Preference remains usable without storage. */
    }
  }
  async function navigate(next: View) {
    view = next;
    selectedId = '';
    requestMode = false;
    error = null;
    notice = null;
    history.replaceState(null, '', `/?view=${next}`);
    await tick();
    document.getElementById('page-title')?.focus();
    if (next === 'history') {
      const current = generation;
      try {
        const latestEvents = await api.history(children);
        if (generation === current && !stopped) events = latestEvents;
      } catch (e) {
        error = errorKey(e);
      }
    }
  }
  async function select(child: Child, request = false) {
    selectedId = child.id;
    requestMode = request;
    error = null;
    notice = null;
    await tick();
    document.getElementById('change-heading')?.focus();
  }
  async function sendIntent(intent: Intent) {
    if (busy) return;
    busy = true;
    error = null;
    notice = null;
    try {
      remember(intent);
      uncertain = intent;
      const result = await api.request(
        api.path(intent.child, `commands/${intent.action}`),
        receipt,
        'POST',
        intent.body,
        intent.key,
      );
      remember(null);
      uncertain = null;
      notice =
        result.operation.status in { applied: 1, failed: 1, expired: 1 }
          ? (result.operation.status as Message)
          : 'queued';
      await refresh();
    } catch (e) {
      error = errorKey(e);
      // A definite rejection cannot have queued a command. Timeouts and malformed
      // success responses must retain their idempotency key for the retry.
      if (e instanceof ApiError && e.status >= 400 && e.status < 500) {
        remember(null);
        uncertain = null;
      }
      if (e instanceof ApiError && e.status === 401) expire();
    } finally {
      busy = false;
    }
  }
  async function send(action: Action, body: Command) {
    if (!user || !selected || uncertain) return;
    await sendIntent({
      owner: user.username,
      child: selected.id,
      action,
      body,
      key: crypto.randomUUID(),
    });
  }
  function statusLabel(child: Child): Message {
    if (transportDown || child.status.connectivity !== 'online') return 'offline';
    if (child.status.blocked) return 'blocked';
    if (child.status.insideUnmeteredWindow) return 'homeworkNow';
    if (child.status.phase === 'expired') return 'exhausted';
    if (child.status.phase === 'outside_schedule') return 'restricted';
    return 'accessAllowed';
  }
  function remaining(child: Child): string {
    if (transportDown || child.status.connectivity !== 'online') return t('unknown');
    if (child.status.unlimitedToday) return t('unlimited');
    return child.status.remainingPlayTimeSeconds == null
      ? t('unknown')
      : `${Math.ceil(child.status.remainingPlayTimeSeconds / 60)} ${t('min')}`;
  }
  onMount(() => {
    try {
      language =
        localStorage.getItem('family.language') === 'de' ||
        (!localStorage.getItem('family.language') && navigator.language.startsWith('de'))
          ? 'de'
          : 'en';
    } catch {
      language = 'en';
    }
    setLanguage();
    const requested = new URL(location.href).searchParams.get('view');
    if (views.includes(requested as View)) view = requested as View;
    void initialize();
    const visibility = () => {
      if (!document.hidden) void refresh();
    };
    const beforeUnload = (event: BeforeUnloadEvent) => {
      if (uncertain) event.preventDefault();
    };
    document.addEventListener('visibilitychange', visibility);
    window.addEventListener('beforeunload', beforeUnload);
    return () => {
      stopped = true;
      generation++;
      stopLive();
      document.removeEventListener('visibilitychange', visibility);
      window.removeEventListener('beforeunload', beforeUnload);
    };
  });
</script>

<svelte:head><title>{user ? `${t(view)} · ` : ''}Family Controls</title></svelte:head>
<a class="skip-link" href="#main">{t('skip')}</a>
{#if !user}
  <div class="language-corner">
    <label
      >{t('language')}<select
        aria-label={t('language')}
        bind:value={language}
        onchange={setLanguage}
        ><option value="en">English</option><option value="de">Deutsch</option></select
      ></label
    >
  </div>
  <main id="main">
    {#if loading}<div class="callback" role="status">
        <h1>Family Controls</h1>
        <p>{t('loading')}</p>
      </div>
    {:else}{#if error}<div class="auth-alert alert error" role="alert">
          {t(error)}
          {#if !config}<button onclick={initialize}>{t('retry')}</button>{/if}
        </div>{/if}{#if config}<Auth {config} {language} onlogin={login} />{/if}{/if}
  </main>
{:else}
  <div class="app-shell">
    <aside class="rail">
      <a class="brand" href="/">Family Controls</a>
      <p class="rail-subtitle">{t('welcome')}</p>
      <nav aria-label="Family Controls">
        {#each views as item}<button
            class:active={view === item}
            aria-current={view === item ? 'page' : undefined}
            onclick={() => navigate(item)}
            >{t(item)}{#if item === 'requests' && pending.length}<span class="count"
                >{pending.length}</span
              >{/if}</button
          >{/each}
      </nav>
      <div class="rail-footer">
        <strong>{user.username}</strong><span>{t(user.role)}</span><label
          >{t('language')}<select
            aria-label={t('language')}
            bind:value={language}
            onchange={setLanguage}
            ><option value="en">English</option><option value="de">Deutsch</option></select
          ></label
        ><button disabled={busy} onclick={logout}>{t('signout')}</button>
      </div>
    </aside>
    <main class="workspace" id="main">
      <header class="page-header">
        <div>
          <h1 id="page-title" tabindex="-1">{t(view === 'today' ? 'title' : view)}</h1>
          <p>
            {t(
              view === 'today'
                ? 'subtitle'
                : view === 'history'
                  ? 'historyHint'
                  : view === 'schedules'
                    ? 'routineHint'
                    : 'welcome',
            )}
          </p>
        </div>
        <div class="connection">
          <span class:connected={live}>{t(live ? 'live' : 'reconnecting')}</span><button
            class="text-button"
            onclick={refresh}>{t('refresh')}</button
          >
        </div>
      </header>
      {#if error}<p class="alert error" role="alert">{t(error)}</p>{/if}
      {#if transportDown}<div class="alert warning" role="status">
          {t('connectionLost')} <button onclick={refresh}>{t('retry')}</button>
        </div>{/if}
      {#if notice}<p class="alert" role="status">
          {t(notice)}
          <button class="text-button" onclick={() => navigate('history')}>{t('history')}</button>
        </p>{/if}
      {#if uncertain}<div class="alert warning" role="alert">
          <p>{uncertain.child} · {actionLabel(language, uncertain.action)}</p>
          <p>{t('uncertain')}</p>
          <button disabled={busy} onclick={() => uncertain && sendIntent(uncertain)}
            >{t('retryChange')}</button
          >
        </div>{/if}
      {#if view === 'today' || view === 'requests'}
        {#if view === 'today' && pending.length}<section class="request-banner">
            <div>
              <h2>{pending[0].id} {t('asking')}</h2>
              <p>{pending[0].status.pendingRequest?.reason} · {pending[0].policy.device}</p>
            </div>
            <button class="primary" onclick={() => select(pending[0], true)}
              >{t('review')}{pending.length > 1 ? ` (${pending.length})` : ''}</button
            >
          </section>{/if}
        <div class="family-layout" class:editing={selected !== undefined}>
          <section class="family-list">
            {#if view === 'today'}<div class="section-heading">
                <h2>{t('children')}</h2>
                <span class="help">{deviceNames.length} {t('computers')}</span>
              </div>
              <div class="filters">
                <label>{t('search')}<input type="search" bind:value={search} /></label><label
                  >{t('computers')}<select aria-label={t('computers')} bind:value={computer}
                    ><option value="">{t('allComputers')}</option>{#each deviceNames as name}<option
                        value={name}>{name}</option
                      >{/each}</select
                  ></label
                >
              </div>{/if}
            {#each view === 'requests' ? pending : visible as child (child.id)}
              <article class="child-row">
                <div class="person">
                  <span class="avatar" aria-hidden="true"
                    >{child.id.slice(0, 1).toLocaleUpperCase()}</span
                  >
                  <div>
                    <h3>{child.id}</h3>
                    <p class="help">{child.policy.device} · {t('account')}: {child.policy.user}</p>
                  </div>
                  <span class="status-label">{t(statusLabel(child))}</span>
                </div>
                {#if view === 'requests'}<blockquote>
                    {child.status.pendingRequest?.reason}
                  </blockquote>
                  <p class="help">
                    {child.status.pendingRequest?.minutes ?? '—'}
                    {t('minutes')} · {dateTime(
                      language,
                      child.status.pendingRequest?.requestedAt ?? null,
                      child.policy.timezone,
                    )}
                  </p>
                  <button class="primary" onclick={() => select(child, true)}>{t('review')}</button>
                {:else}<div class="time-row">
                    <div>
                      <strong class="time-value">{remaining(child)}</strong><span>
                        {t('remaining')}</span
                      >
                    </div>
                    <button onclick={() => select(child)}>{t('change')}</button>
                  </div>
                  {#if !transportDown && child.status.connectivity === 'online' && child.status.effectiveDailyLimitMinutes != null && child.status.remainingPlayTimeSeconds != null && !child.status.unlimitedToday}
                    {@const used = Math.max(
                      0,
                      child.status.effectiveDailyLimitMinutes -
                        child.status.remainingPlayTimeSeconds / 60,
                    )}
                    <progress
                      max={Math.max(1, child.status.effectiveDailyLimitMinutes)}
                      value={used}
                      aria-label={`${Math.floor(used)} ${t('of')} ${child.status.effectiveDailyLimitMinutes} ${t('minutes')} ${t('used')}`}
                    ></progress>
                    <p class="help">
                      {Math.floor(used)}
                      {t('of')}
                      {child.status.effectiveDailyLimitMinutes}
                      {t('minutes')}
                      {t('used')}
                    </p>
                  {/if}
                  {#if !transportDown && child.status.connectivity === 'online' && child.status.allowedUntil}<p
                      class="help"
                    >
                      {t('until')}
                      {child.status.allowedUntil} · {child.policy.timezone}
                    </p>{:else}<p class="help">
                      {t('lastSeen')}: {dateTime(
                        language,
                        child.status.lastSeen,
                        child.policy.timezone,
                      )}
                    </p>{/if}
                  {#if child.status.delivery.pending || child.status.policyDelivery === 'pending'}<p
                      class="state-note"
                    >
                      {t('queued')}
                    </p>{/if}
                  {#if child.status.application.error}<p class="alert error">
                      {t('failed')} · {child.status.application.error}
                    </p>{/if}
                  {#if child.status.muted}<p class="help">{t('muted')}</p>{/if}
                {/if}
              </article>
            {:else}<p class="empty">
                {t(
                  view === 'requests' ? 'noRequests' : children.length ? 'noMatches' : 'noChildren',
                )}
              </p>{/each}
            <p class="help footnote">{t('offlineHint')}</p>
          </section>
          {#if selected}{#key `${selected.id}:${requestMode}:${requestMode ? selected.status.pendingRequest?.id : ''}`}<div
                class="editor-slot"
              >
                <CommandEditor
                  child={selected}
                  {language}
                  request={requestMode}
                  {busy}
                  locked={uncertain !== null ||
                    transportDown ||
                    selected.status.delivery.pending > 0}
                  onsend={send}
                  onclose={() => (selectedId = '')}
                />
              </div>{/key}{/if}
        </div>
      {:else if view === 'schedules'}
        <label class="short-field"
          >{t('account')}<select aria-label={t('account')} bind:value={selectedId}
            ><option value="">—</option>{#each children as child}<option value={child.id}
                >{child.id} · {child.policy.device}</option
              >{/each}</select
          ></label
        >
        {#if selected}{#key selected.id}<PolicyEditor
              child={selected}
              {language}
              onchanged={refresh}
            />{/key}{/if}
      {:else if view === 'history'}
        <label class="short-field"
          >{t('account')}<select aria-label={t('account')} bind:value={selectedId}
            ><option value="">{t('children')}</option>{#each children as child}<option
                value={child.id}>{child.id} · {child.policy.device}</option
              >{/each}</select
          ></label
        >
        <ul class="history-list">
          {#each events.filter((e) => !selectedId || e.child === selectedId) as event (event.details.operationId)}<li
            >
              <div>
                <h2>{event.child} · {actionLabel(language, event.action)}</h2>
                <p class="help">
                  {dateTime(language, event.at)} · {t('actor')}: {event.details.actor}
                </p>
                {#if event.error}<p>{event.error}</p>{/if}
                <details>
                  <summary>{t('outcome')}</summary>
                  <p class="operation-id">{event.details.operationId}</p>
                </details>
              </div>
              <span
                class="badge"
                class:warning={['queued', 'accepted'].includes(event.outcome)}
                class:failure={event.outcome === 'failed'}
                >{actionLabel(language, event.outcome)}</span
              >
            </li>{:else}<li class="empty">{t('noHistory')}</li>{/each}
        </ul>
      {:else}
        <section>
          <h2>{t('preferences')}</h2>
          <label class="short-field"
            >{t('language')}<select
              aria-label={t('language')}
              bind:value={language}
              onchange={setLanguage}
              ><option value="en">English</option><option value="de">Deutsch</option></select
            ></label
          >
          <p class="help">{t('deviceHint')}</p>
        </section>
        {#if user.role === 'admin'}<Admin {children} {language} current={user.username} />{/if}
      {/if}
    </main>
  </div>
{/if}
