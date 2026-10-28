<script lang="ts">
  import { childName } from '../contracts';
  import { assignmentLabel } from '#lib/contracts.ts';
  import { untrack } from 'svelte';
  import { actions, type Child, type Action, type Command } from '../contracts';
  import { translate, type Language, type Message } from '../i18n';
  let {
    child,
    language,
    request = false,
    busy,
    locked,
    onsend,
    onclose,
  }: {
    child: Child;
    language: Language;
    request?: boolean;
    busy: boolean;
    locked: boolean;
    onsend: (action: Action, body: Command) => Promise<void>;
    onclose: () => void;
  } = $props();
  let action = $state<Action>('add-time');
  let decision = $state('add-time');
  let minutes = $state<number | undefined>(
    untrack(() => (request ? (child.status.pendingRequest?.minutes ?? 20) : 20)),
  );
  let until = $state('19:00'),
    reason = $state(''),
    duration = $state('today'),
    days = $state<number | undefined>(1),
    confirmed = $state(false);
  const t = (key: Message) => translate(language, key);
  const effective = $derived(request ? decision : action);
  const needsMinutes = $derived(
    [
      'add-time',
      'deduct-time',
      'set-limit',
      'set-remaining',
      'grant-session',
      'extend-access',
      'reduce-access',
    ].includes(effective),
  );
  const broad = $derived(
    ['allow-today', 'unlimited-today', 'restore-today', 'block'].includes(effective),
  );
  const fresh = $derived(
    child.status.connectivity === 'online' &&
      child.status.application.status === 'applied' &&
      child.status.remainingPlayTimeSeconds != null,
  );
  const remaining = $derived(
    fresh && child.status.remainingPlayTimeSeconds != null
      ? Math.ceil(child.status.remainingPlayTimeSeconds / 60)
      : null,
  );
  const budgetChange = $derived(
    ['add-time', 'deduct-time', 'set-limit', 'set-remaining'].includes(effective),
  );
  const total = $derived(fresh ? child.status.effectiveDailyLimitMinutes : null);
  const left = $derived(
    fresh && !child.status.unlimitedToday ? child.status.remainingPlayTimeSeconds! / 60 : null,
  );
  // The ledger retains usage beyond the allowance; a clamped balance does not.
  const consumed = $derived(fresh ? child.status.reportedUsedSeconds / 60 : null);
  const preview = $derived.by(() => {
    if (minutes == null) return { total: null, remaining: null };
    if (effective === 'set-limit')
      return {
        total: minutes,
        remaining: minutes === 0 ? 0 : consumed == null ? null : Math.max(0, minutes - consumed),
      };
    if (total == null || left == null) return { total: null, remaining: null };
    if (effective === 'add-time')
      return {
        total: total + minutes,
        remaining: consumed == null ? null : Math.max(0, total + minutes - consumed),
      };
    if (effective === 'deduct-time')
      return { total: total - Math.min(minutes, left), remaining: Math.max(0, left - minutes) };
    if (effective === 'set-remaining')
      return {
        total: consumed == null ? null : consumed + minutes,
        remaining: minutes,
      };
    return { total: null, remaining: null };
  });
  const heading = $derived<Message>(
    request
      ? 'review'
      : ['mute', 'unmute', 'unblock'].includes(effective)
        ? 'persistentTitle'
        : effective === 'block' && duration === 'days'
          ? 'daysTitle'
          : 'editTitle',
  );
  const formatMinutes = (value: number | null) =>
    value == null ? t('unknown') : `${Math.ceil(value)} ${t('min')}`;
  const hint = $derived<Message>(
    effective === 'unlimited-today'
      ? 'unlimitedHint'
      : effective === 'allow-today'
        ? 'allowHint'
        : effective === 'restore-today'
          ? 'restoreHint'
          : effective === 'grant-session'
            ? 'sessionHint'
            : ['allow-until', 'extend-access', 'reduce-access'].includes(effective)
              ? 'accessHint'
              : effective === 'block'
                ? 'blockHint'
                : ['mute', 'unmute', 'unblock'].includes(effective)
                  ? 'persistentHint'
                  : 'scheduleUnchanged',
  );
  async function submit(event: SubmitEvent) {
    event.preventDefault();
    if (locked || busy || (broad && !confirmed) || (effective === 'set-remaining' && !fresh))
      return;
    const body: Command = {};
    if (needsMinutes) body.minutes = minutes;
    if (effective === 'allow-until') body.until = until;
    if (effective === 'block') {
      if (duration === 'today') body.today = true;
      if (duration === 'days') body.days = days;
    }
    if (child.status.revision !== undefined) body.expectedRevision = child.status.revision;
    if (request) {
      if (!child.status.pendingRequest) return;
      body.requestId = child.status.pendingRequest.id;
      body.decision = decision;
      body.reason = reason;
    }
    await onsend(request ? 'decide-request' : action, body);
  }
</script>

<section class="change-panel" aria-labelledby="change-heading">
  <div class="section-heading">
    <h2 id="change-heading" tabindex="-1">{t(heading)}</h2>
    <button class="text-button" onclick={onclose}>{t('close')}</button>
  </div>
  <p class="help">{childName(child)} · {assignmentLabel(child)}</p>
  {#if request && !child.status.pendingRequest}<p role="status">{t('requestExpired')}</p>
  {:else}
    {#if request && child.status.pendingRequest}<blockquote>
        {child.status.pendingRequest.reason}
      </blockquote>{/if}
    <form onsubmit={submit}>
      {#if request}<label
          >{t('decision')}<select
            aria-label={t('decision')}
            bind:value={decision}
            onchange={() => (confirmed = false)}
            >{#each ['add-time', 'grant-session', 'unlimited-today', 'allow-today', 'deny'] as value}<option
                {value}>{t(value as Message)}</option
              >{/each}</select
          ></label
        >
      {:else}<label
          >{t('operation')}<select
            aria-label={t('operation')}
            bind:value={action}
            onchange={() => (confirmed = false)}
            >{#each actions as value}<option {value}>{t(value)}</option>{/each}</select
          ></label
        >{/if}
      {#if needsMinutes}
        <div class="presets" aria-label={t('minutes')}>
          {#each [15, 30, 60] as amount}<button
              type="button"
              aria-pressed={minutes === amount}
              onclick={() => (minutes = amount)}>{amount} {t('min')}</button
            >{/each}
        </div>
        <label
          >{t('custom')}<input
            type="number"
            bind:value={minutes}
            min={['set-limit', 'set-remaining'].includes(effective) ? 0 : 1}
            max="1440"
            step="1"
            required
          /></label
        >
      {/if}
      {#if effective === 'allow-until'}<label
          >{t('clockTime')}<input
            bind:value={until}
            required
            pattern="([01][0-9]|2[0-3]):[0-5][0-9]|24:00"
            placeholder="19:00"
          /></label
        >{/if}
      {#if effective === 'block'}<label
          >{t('blockDuration')}<select
            aria-label={t('blockDuration')}
            bind:value={duration}
            onchange={() => (confirmed = false)}
            ><option value="today">{t('todayOnly')}</option><option value="days">{t('days')}</option
            ></select
          ></label
        >{#if duration === 'days'}<label
            >{t('days')}<input
              type="number"
              bind:value={days}
              min="1"
              max="31"
              step="1"
              required
            /></label
          >{/if}{/if}
      {#if request}<label
          >{t('reason')}<textarea bind:value={reason} maxlength="300" rows="2"></textarea></label
        >{/if}
      <div class="change-preview">
        <h3>{t('preview')}</h3>
        {#if budgetChange}<p class="value-pair">
            <span>{t('allowance')}</span><strong
              >{fresh && child.status.unlimitedToday ? t('unlimited') : formatMinutes(total)} → {formatMinutes(
                preview.total,
              )}</strong
            >
          </p>
          <p class="value-pair">
            <span>{t('timeRemaining')}</span><strong
              >{fresh && child.status.unlimitedToday ? t('unlimited') : (remaining ?? t('unknown'))} →
              {formatMinutes(preview.remaining)}</strong
            >
          </p>
          <p class="help">{t('consumedHint')}</p>
          {#if preview.remaining == null}<p class="help">{t('unknownPreview')}</p>{/if}
          <p class="help">{t('estimateHint')}</p>{/if}
        <p>{t(hint)}</p>
        {#if !['mute', 'unmute', 'unblock', 'block', 'deny'].includes(effective)}<p class="help">
            {t('expires')} ({child.policy.timezone})
          </p>{/if}
        {#if effective === 'set-remaining' && !fresh}<p class="alert">{t('connectionLost')}</p>{/if}
        {#if broad}<label class="check"
            ><input type="checkbox" bind:checked={confirmed} required />{t('confirm')}</label
          >{/if}
      </div>
      <button
        class="primary full"
        disabled={busy ||
          locked ||
          (effective === 'set-remaining' && !fresh) ||
          (broad && !confirmed)}>{t(busy ? 'sending' : request ? 'sendDecision' : 'apply')}</button
      >
      <p class="help">{t('pendingHint')}</p>
    </form>
  {/if}
</section>
