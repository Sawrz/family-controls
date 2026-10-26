<script lang="ts">
  import { untrack } from 'svelte';
  import { Api, errorKey } from '#lib/api.ts';
  import { weeklyUsage, type Child, type WeeklyUsage } from '#lib/contracts.ts';
  import { translate, type Language, type Message } from '#lib/i18n.ts';

  let {
    children,
    language,
    refreshVersion,
  }: { children: Child[]; language: Language; refreshVersion: number } = $props();
  let childId = $state(untrack(() => children[0]?.id ?? ''));
  let start = $state(''),
    report = $state<WeeklyUsage | null>(null);
  let loading = $state(false),
    error = $state<Message | null>(null);
  let generation = 0;
  const api = new Api();
  const t = (key: Message) => translate(language, key);
  const maximum = $derived(Math.max(60, ...(report?.days.map((d) => d.playSeconds ?? 0) ?? [])));
  const minutes = (seconds: number | null) =>
    seconds == null ? '—' : `${Math.floor(seconds / 60)} ${t('min')}`;
  const dayLabel = (day: string) =>
    new Intl.DateTimeFormat(language, {
      weekday: 'short',
      day: 'numeric',
      month: 'short',
      timeZone: 'UTC',
    }).format(new Date(`${day}T12:00:00Z`));
  function shifted(day: string, offset: number): string {
    const value = new Date(`${day}T12:00:00Z`);
    value.setUTCDate(value.getUTCDate() + offset);
    return value.toISOString().slice(0, 10);
  }
  async function load(child: string, week: string) {
    const current = ++generation;
    if (!child) {
      report = null;
      error = null;
      loading = false;
      return;
    }
    loading = true;
    error = null;
    try {
      const result = await api.request(
        api.path(child, 'usage') + (week ? `?start=${week}` : ''),
        weeklyUsage,
      );
      if (current === generation) report = result;
    } catch (e) {
      if (current === generation) {
        error = errorKey(e);
        report = null;
      }
    } finally {
      if (current === generation) loading = false;
    }
  }
  $effect(() => {
    if (!children.some((child) => child.id === childId)) {
      childId = children[0]?.id ?? '';
      start = '';
    }
  });
  $effect(() => {
    refreshVersion;
    void load(childId, start);
  });
</script>

<section aria-busy={loading}>
  <p>{t('usageIntro')}</p>
  {#if children.length === 0}
    <p role="status">{t('noUsageChildren')}</p>
  {:else}
    <label class="short-field"
      >{t('account')}
      <select aria-label={t('account')} bind:value={childId} onchange={() => (start = '')}>
        {#each children as child}<option value={child.id}>{child.id}</option>{/each}
      </select>
    </label>
  {/if}
  {#if error}<p class="alert error" role="alert">{t(error)}</p>
    <button onclick={() => load(childId, start)}>{t('refresh')}</button>{/if}
  {#if loading}<p role="status">{t('loading')}</p>{/if}
  {#if report && !loading}
    <div class="week-controls">
      <button
        onclick={() => report && (start = shifted(report.start, -7))}
        disabled={new Date(report.start).getTime() < Date.now() - 359 * 86400000}
        >{t('previousWeek')}</button
      >
      <h2>{dayLabel(report.start)} – {dayLabel(shifted(report.start, 6))}</h2>
      <button
        onclick={() => report && (start = shifted(report.start, 7))}
        disabled={new Date(shifted(report.start, 7)).getTime() > Date.now()}>{t('nextWeek')}</button
      >
    </div>
    <p class="help">{t('usageExplanation')}</p>
    <p class="state-note">{t('usageIncomplete')}</p>
    <ol class="usage-days">
      {#each report.days as day}
        <li>
          <div class="day-heading">
            <h3>{dayLabel(day.day)}</h3>
            <strong>{minutes(day.playSeconds)} {t('meteredUsage')}</strong>
          </div>
          {#if day.playSeconds != null}
            <progress
              max={maximum}
              value={day.playSeconds}
              aria-label={`${dayLabel(day.day)} · ${t('meteredUsage')}: ${minutes(day.playSeconds)}`}
            ></progress>
            <p class="help">
              {t('activeUsage')}: {minutes(day.activeSeconds)} · {t('unmeteredUsage')}: {minutes(
                day.unmeteredSeconds,
              )}
            </p>
            <details>
              <summary>{t('deviceDetails')}</summary>
              <ul>
                {#each day.devices as device}<li>
                    <strong>{device.device} · {device.user}</strong>
                    <p>
                      {t('meteredUsage')}: {minutes(device.playSeconds)} · {t('activeUsage')}: {minutes(
                        device.activeSeconds,
                      )} · {t('unmeteredUsage')}: {minutes(device.unmeteredSeconds)}
                    </p>
                  </li>{/each}
              </ul>
            </details>
          {/if}
          <p class="help">
            {t(
              day.availability === 'missing'
                ? 'missingUsage'
                : day.availability === 'future'
                  ? 'futureUsage'
                  : 'partialUsage',
            )}
          </p>
        </li>
      {/each}
    </ol>
  {/if}
</section>

<style>
  .week-controls,
  .day-heading {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 1rem;
    flex-wrap: wrap;
  }
  .week-controls {
    margin: 2rem 0 1rem;
  }
  .week-controls h2,
  .day-heading h3 {
    margin: 0;
  }
  .usage-days {
    list-style: none;
    padding: 0;
  }
  .usage-days > li {
    padding: 1.5rem 0;
    border-bottom: 1px solid var(--line);
  }
  .usage-days progress {
    margin-top: 1rem;
  }
  .usage-days strong {
    font-variant-numeric: tabular-nums;
  }
  .usage-days details {
    margin-top: 0.75rem;
  }
  @media (max-width: 540px) {
    .week-controls h2 {
      flex-basis: 100%;
      order: -1;
    }
  }
</style>
