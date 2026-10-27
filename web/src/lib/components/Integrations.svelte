<script lang="ts">
  import { onMount } from 'svelte';
  import { z } from 'zod';
  import { Api, errorKey } from '../api';
  import { languagePreferences, integration, linkChallenge, type Integration } from '../contracts';
  import { translate, dateTime, type Language, type Message } from '../i18n';

  let { language, admin = false }: { language: Language; admin?: boolean } = $props();
  let items = $state<Integration[]>([]),
    loading = $state(true),
    busy = $state(false);
  let error = $state<Message | null>(null),
    notice = $state<Message | null>(null);
  let preferences = $state<z.infer<typeof languagePreferences>>({
    defaultLanguage: 'en',
    chatLanguages: {},
  });
  let languageLoaded = $state(false);
  let codes = $state<Record<string, string>>({});
  let removing = $state<{ kind: string; externalId: string; label: string } | null>(null);
  const api = new Api(),
    t = (key: Message) => translate(language, key);
  async function refresh() {
    try {
      items = (
        await api.request(
          admin ? '/api/v1/integrations' : '/api/v1/me/connections',
          z.object({ integrations: z.array(integration) }),
        )
      ).integrations;
      if (admin && !languageLoaded) {
        preferences = await api.request('/api/v1/preferences', languagePreferences);
        languageLoaded = true;
      }
      error = null;
    } catch (e) {
      error = errorKey(e);
    } finally {
      loading = false;
    }
  }
  onMount(() => {
    void refresh();
    const timer = setInterval(() => {
      if (!document.hidden && !busy) void refresh();
    }, 5000);
    return () => clearInterval(timer);
  });
  async function act(task: () => Promise<unknown>, message: Message) {
    if (busy) return;
    busy = true;
    error = null;
    notice = null;
    try {
      await task();
      notice = message;
      removing = null;
      await refresh();
    } catch (e) {
      error = errorKey(e);
    } finally {
      busy = false;
    }
  }
  function saveLanguage(kind?: string) {
    return act(async () => {
      const current = await api.request('/api/v1/preferences', languagePreferences);
      if (kind) {
        const selected = preferences.chatLanguages[kind];
        if (selected) current.chatLanguages[kind] = selected;
        else delete current.chatLanguages[kind];
      } else current.defaultLanguage = preferences.defaultLanguage;
      await api.request('/api/v1/preferences', languagePreferences, 'PUT', current);
    }, 'languageSaved');
  }
  function begin(kind: string) {
    return act(async () => {
      const result = await api.request(
        `/api/v1/me/connections/${kind}/challenge`,
        linkChallenge.extend({ token: z.string() }),
        'POST',
      );
      codes[kind] = result.token;
    }, 'linkCodeReady');
  }
</script>

{#if error}<p class="alert error" role="alert">
    {t(error)} <button onclick={refresh}>{t('retry')}</button>
  </p>{/if}
{#if notice}<p class="alert" role="status">{t(notice)}</p>{/if}
{#if loading}<p role="status">{t('loading')}</p>{/if}
{#if admin && languageLoaded}
  <section class="settings-section">
    <h3>{t('householdLanguage')}</h3>
    <p class="help">{t('languageHint')}</p>
    <label
      >{t('householdLanguage')}<select
        aria-label={t('householdLanguage')}
        bind:value={preferences.defaultLanguage}
        ><option value="en">English</option><option value="de">Deutsch</option></select
      ></label
    >
    <button disabled={busy} onclick={() => saveLanguage()}>{t('saveLanguage')}</button>
  </section>
{/if}
{#each items as item}
  <section class="settings-section" aria-label={item.kind === 'telegram' ? 'Telegram' : 'Matrix'}>
    <div class="section-heading">
      <h3>{item.kind === 'telegram' ? 'Telegram' : 'Matrix'}</h3>
      <span class="badge" class:warning={item.status !== 'connected'}>{t(item.status)}</span>
    </div>
    <dl class="settings-details">
      <div>
        <dt>{t('integrationBot')}</dt>
        <dd>{item.bot ?? t('unknown')}</dd>
      </div>
      <div>
        <dt>{t(item.kind === 'telegram' ? 'parentGroup' : 'matrixRoom')}</dt>
        <dd>{item.destination ?? t(item.configured ? 'unknown' : 'notConfigured')}</dd>
      </div>
      <div>
        <dt>{t('lastContact')}</dt>
        <dd>{item.lastSuccess ? dateTime(language, item.lastSuccess) : t('neverSeen')}</dd>
      </div>
    </dl>
    {#if admin && item.configured && languageLoaded}
      <label
        >{t('groupLanguage')}<select
          aria-label={t('groupLanguage')}
          value={preferences.chatLanguages[item.kind] ?? ''}
          onchange={(event) => {
            const value = event.currentTarget.value;
            if (value === 'en' || value === 'de') preferences.chatLanguages[item.kind] = value;
            else delete preferences.chatLanguages[item.kind];
          }}
          ><option value="">{t('inheritLanguage')}</option><option value="en">English</option
          ><option value="de">Deutsch</option></select
        ></label
      >
      <button disabled={busy} onclick={() => saveLanguage(item.kind)}>{t('saveLanguage')}</button>
    {/if}
    <p class="help">{t(admin ? 'integrationManaged' : 'integrationPersonal')}</p>
    <ul class="record-list">
      {#each item.links as link}
        <li>
          <div>
            {#if admin}<strong>{link.displayName}</strong>{/if}
            <p>{link.label ?? link.externalId}</p>
            {#if link.label}<p class="help">{link.externalId}</p>{/if}
          </div>
          <button
            disabled={busy}
            onclick={() =>
              (removing = {
                kind: item.kind,
                externalId: link.externalId,
                label: `${admin ? link.displayName + ' · ' : ''}${link.label ?? link.externalId}`,
              })}>{t('disconnect')}</button
          >
        </li>
      {:else}<li>{t(admin ? 'noLinkedParents' : 'notLinked')}</li>{/each}
    </ul>
    {#if removing?.kind === item.kind}
      <div class="alert warning">
        <p>{t('confirmDisconnect')} <strong>{removing.label}</strong>?</p>
        <p>{t('disconnectHint')}</p>
        <div class="form-actions">
          <button
            class="danger"
            disabled={busy}
            onclick={() =>
              removing &&
              act(
                () =>
                  api.request(
                    `${admin ? '/api/v1/integrations' : '/api/v1/me/connections'}/${item.kind}${admin ? '/links' : ''}?external_id=${encodeURIComponent(removing!.externalId)}`,
                    z.object({ status: z.string() }),
                    'DELETE',
                  ),
                'disconnected',
              )}>{t('disconnect')}</button
          ><button onclick={() => (removing = null)}>{t('cancel')}</button>
        </div>
      </div>
    {/if}
    {#if !admin && item.configured}
      {#if item.challenge}
        <div class="alert">
          {#if item.challenge.externalId}
            <p>
              {t('detectedAccount')}:
              <strong>{item.challenge.label ?? item.challenge.externalId}</strong>
            </p>
            <p class="help">{item.challenge.externalId}</p>
            <p>{t('confirmOwnAccount')}</p>
            <button
              class="primary"
              disabled={busy}
              onclick={() =>
                act(async () => {
                  await api.request(
                    `/api/v1/me/connections/${item.kind}/confirm`,
                    z.object({ status: z.string() }),
                    'POST',
                    {
                      challengeId: item.challenge!.challengeId,
                      externalId: item.challenge!.externalId,
                    },
                  );
                  delete codes[item.kind];
                }, 'linked')}>{t('confirmLink')}</button
            >
          {:else}
            <p>{t(item.kind === 'telegram' ? 'telegramLinkSteps' : 'matrixLinkSteps')}</p>
            {#if codes[item.kind]}<label
                >{t('linkCommand')}<input
                  readonly
                  autocomplete="off"
                  value={`${item.kind === 'telegram' ? '/link' : '!link'} ${codes[item.kind]}`}
                /></label
              >{:else}<p>{t('restartLink')}</p>{/if}
          {/if}
          <p class="help">{t('linkExpires')}: {dateTime(language, item.challenge.expires)}</p>
          <button
            disabled={busy}
            onclick={() =>
              act(async () => {
                await api.request(
                  `/api/v1/me/connections/${item.kind}/challenge`,
                  z.object({ status: z.string() }),
                  'DELETE',
                );
                delete codes[item.kind];
              }, 'linkCancelled')}>{t('cancel')}</button
          >
        </div>
      {:else}<button disabled={busy} onclick={() => begin(item.kind)}>{t('connectAccount')}</button
        >{/if}
    {:else if !admin}<p>{t('askAdminIntegration')}</p>{/if}
  </section>
{/each}
