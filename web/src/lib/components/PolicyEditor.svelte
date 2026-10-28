<script lang="ts">
  import { childName } from '../contracts';
  import { assignmentLabel } from '#lib/contracts.ts';
  import { untrack } from 'svelte';
  import { Api, ApiError, errorKey } from '../api';
  import { policy, type Child } from '../contracts';
  import { translate, type Language, type Message } from '../i18n';
  let {
    child,
    language,
    onchanged,
  }: { child: Child; language: Language; onchanged: () => Promise<void> } = $props();
  let draft = $state(untrack(() => structuredClone($state.snapshot(child.policy))));
  const windowStrings = (mapping: Record<string, string[]>) =>
    Array.from({ length: 7 }, (_, i) => (mapping[String(i + 1)] ?? mapping.ALL ?? []).join(', '));
  let allowed = $state(untrack(() => windowStrings(draft.policy.timeWindows.allowed)));
  let homework = $state(untrack(() => windowStrings(draft.policy.timeWindows.unmetered)));
  let activities = $state(untrack(() => draft.policy.playTime.activities.join('\n')));
  let busy = $state(false),
    conflict = $state(false),
    error = $state<Message | null>(null),
    notice = $state(false);
  const api = new Api(),
    t = (key: Message) => translate(language, key);
  async function reload() {
    busy = true;
    error = null;
    try {
      draft = await api.request(api.path(child.id, 'policy'), policy);
      allowed = windowStrings(draft.policy.timeWindows.allowed);
      homework = windowStrings(draft.policy.timeWindows.unmetered);
      activities = draft.policy.playTime.activities.join('\n');
      conflict = false;
      notice = false;
    } catch (e) {
      error = errorKey(e);
    } finally {
      busy = false;
    }
  }
  async function save(event: SubmitEvent) {
    event.preventDefault();
    busy = true;
    error = null;
    notice = false;
    try {
      const windows = (values: string[]) =>
        Object.fromEntries(
          values.map((value, i) => [
            String(i + 1),
            value
              .split(',')
              .map((v) => v.trim())
              .filter(Boolean),
          ]),
        );
      const revision = draft.revision;
      const body = structuredClone(
        $state.snapshot({
          assignments: draft.assignments,
          timezone: draft.timezone,
          displayName: draft.displayName || null,
          policy: draft.policy,
        }),
      );
      body.policy.timeWindows = { allowed: windows(allowed), unmetered: windows(homework) };
      body.policy.playTime.activities = activities
        .split('\n')
        .map((v) => v.trim())
        .filter(Boolean);
      draft = await api.request(
        api.path(child.id, `policy?revision=${revision}`),
        policy,
        'PUT',
        body,
      );
      notice = true;
      await onchanged();
    } catch (e) {
      error = errorKey(e);
      conflict = e instanceof ApiError && e.status === 409;
    } finally {
      busy = false;
    }
  }
</script>

<section class="policy-editor">
  <h2>{childName(child)} · {assignmentLabel(child)}</h2>
  <p class="help">{t('routineHint')} · {child.policy.timezone}</p>
  {#if error}<p role="alert" class="alert error">{t(conflict ? 'draftConflict' : error)}</p>{/if}
  {#if notice}<p role="status" class="alert">{t('saved')}</p>{/if}
  <form onsubmit={save}>
    <label
      >{t('displayName')}<input
        bind:value={draft.displayName}
        maxlength="100"
        placeholder={childName(child)}
      /></label
    >
    <label class="short-field"
      >{t('allowance')} ({t('minutes')})<input
        type="number"
        min="0"
        max="1440"
        step="1"
        bind:value={draft.policy.playTime.dailyLimitMinutes}
        required
      /></label
    >
    <p class="help" id="window-help">{t('windowsHint')}</p>
    <div class="schedule-grid" aria-describedby="window-help">
      {#each allowed as _, i}
        <fieldset>
          <legend>{t(`weekday${i + 1}` as Message)}</legend><label
            >{t('allowed')}<input bind:value={allowed[i]} placeholder="08:00-19:00" /></label
          ><label>{t('homework')}<input bind:value={homework[i]} placeholder="15:00-16:00" /></label
          >
        </fieldset>
      {/each}
    </div>
    <details>
      <summary>{t('advanced')}</summary>
      <label>{t('activities')}<textarea bind:value={activities} rows="3" required></textarea></label
      >
      <label class="check"
        ><input type="checkbox" bind:checked={draft.policy.trackInactive} />{t(
          'trackInactive',
        )}</label
      >
      <label class="check"
        ><input type="checkbox" bind:checked={draft.policy.countLockedTime} />{t(
          'countLockedTime',
        )}</label
      >
      <label class="check"
        ><input type="checkbox" checked={draft.policy.blockTTYLogin} disabled />{t(
          'blockTTYLogin',
        )}</label
      >
      <label class="check"
        ><input type="checkbox" bind:checked={draft.policy.killSessions} />{t(
          'killSessions',
        )}</label
      >
      <label class="short-field"
        >{t('lockoutAction')}<select
          aria-label={t('lockoutAction')}
          bind:value={draft.policy.lockoutAction}
          >{#each ['terminate', 'suspend', 'shutdown', 'lock', 'kill'] as value}<option {value}
              >{t(value as Message)}</option
            >{/each}</select
        ></label
      >
    </details>
    <div class="form-actions">
      <button class="primary" disabled={busy || conflict}
        >{t(busy ? 'sending' : 'savePolicy')}</button
      ><button type="button" disabled={busy} onclick={reload}>{t('reloadPolicy')}</button>
    </div>
  </form>
</section>
