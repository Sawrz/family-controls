<script lang="ts">
  import { childName } from '../contracts';
  import { onMount, untrack } from 'svelte';
  import { Api, ApiError, errorKey } from '../api';
  import { policy, type Child, type Policy } from '../contracts';
  import { translate, type Language, type Message } from '../i18n';
  let {
    child,
    children,
    language,
    onchanged,
  }: { child: Child; children: Child[]; language: Language; onchanged: () => Promise<void> } =
    $props();
  let minutes = $state(60),
    start = $state('08:00'),
    end = $state('19:00'),
    copied = $state<Policy | null>(null);
  let busy = $state(false),
    conflict = $state(false),
    error = $state<Message | null>(null);
  let revision = $state(untrack(() => child.policy.revision));
  let heading: HTMLHeadingElement;
  const api = new Api(),
    t = (key: Message) => translate(language, key);
  onMount(() => heading.focus());
  async function reload() {
    busy = true;
    try {
      const latest = await api.request(api.path(child.id, 'policy'), policy);
      revision = latest.revision;
      await onchanged();
      conflict = false;
      error = null;
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
    if (!copied && start >= end) {
      error = 'invalid';
      busy = false;
      return;
    }
    try {
      await api.request(api.path(child.id, `setup?revision=${revision}`), policy, 'POST', {
        dailyLimitMinutes: minutes,
        timeWindows: copied?.policy.timeWindows ?? {
          allowed: { ALL: [`${start}-${end}`] },
          unmetered: {},
        },
      });
      await onchanged();
    } catch (e) {
      error = errorKey(e);
      conflict = e instanceof ApiError && e.status === 409;
    } finally {
      busy = false;
    }
  }
</script>

<section class="settings-section setup-panel" aria-label={`${t('setupChild')} ${childName(child)}`}>
  <h2 id="change-heading" tabindex="-1" bind:this={heading}>
    {t('setupChild')}: {childName(child)}
  </h2>
  <p>{t('setupIntro')}</p>
  {#if error}<p class="alert error" role="alert">
      {t(error)}{#if conflict}<button disabled={busy} onclick={reload}>{t('reloadSetup')}</button
        >{/if}
    </p>{/if}
  {#if children.some((c) => c.policy.setupComplete)}<div class="form-actions">
      <span>{t('copySettings')}:</span
      >{#each children.filter((c) => c.policy.setupComplete) as source}<button
          disabled={busy}
          onclick={() => {
            copied = structuredClone($state.snapshot(source.policy));
            minutes = source.policy.policy.playTime.dailyLimitMinutes;
          }}>{childName(source)}</button
        >{/each}
    </div>{/if}
  <form class="settings-form" onsubmit={save}>
    <label
      >{t('dailyAllowance')}<input
        type="number"
        bind:value={minutes}
        min="0"
        max="1440"
        step="1"
        required
      /></label
    >
    {#if copied}<p>{t('copiedSchedule')}</p>
      <ul>
        {#each Object.entries(copied.policy.timeWindows.allowed) as [day, windows]}<li>
            {day === 'ALL' ? t('everyDay') : t(('weekday' + day) as Message)}: {windows.join(
              ', ',
            ) || t('noAccess')}
          </li>{/each}
      </ul>
      {#if Object.values(copied.policy.timeWindows.unmetered).some((windows) => windows.length > 0)}
        <p class="help">{t('homework')}</p>
        <ul>
          {#each Object.entries(copied.policy.timeWindows.unmetered).filter(([, windows]) => windows.length > 0) as [day, windows]}<li
            >
              {day === 'ALL' ? t('everyDay') : t(('weekday' + day) as Message)}: {windows.join(
                ', ',
              )}
            </li>{/each}
        </ul>
      {/if}
      <button type="button" onclick={() => (copied = null)}>{t('chooseHours')}</button>
    {:else}<label
        >{t('accessStarts')}<input type="time" bind:value={start} step="60" required /></label
      ><label>{t('accessEnds')}<input type="time" bind:value={end} step="60" required /></label
      >{/if}
    <p class="help">{child.policy.timezone} · {t('setupScope')}</p>
    <button class="primary" disabled={busy || conflict}
      >{t(busy ? 'sending' : 'confirmSetup')}</button
    >
  </form>
</section>
