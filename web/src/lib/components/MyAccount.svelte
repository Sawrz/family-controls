<script lang="ts">
  import { onMount, untrack } from 'svelte';
  import { z } from 'zod';
  import { Api, ApiError, errorKey } from '../api';
  import { account, authentication, type Account } from '../contracts';
  import { translate, type Language, type Message } from '../i18n';
  import Integrations from './Integrations.svelte';
  let {
    user,
    language,
    onchanged,
  }: { user: Account; language: Language; onchanged: (user: Account) => void } = $props();
  let name = $state(untrack(() => user.displayName)),
    selectedLanguage = $state<Language | ''>(untrack(() => user.language ?? ''));
  let auth = $state<z.infer<typeof authentication> | null>(null),
    busy = $state(false);
  let error = $state<Message | null>(null),
    notice = $state<Message | null>(null);
  let currentPassword = $state(''),
    newPassword = $state(''),
    confirmation = $state('');
  const api = new Api(),
    t = (key: Message) => translate(language, key);
  const provider = $derived(auth?.providers.find((p) => p.issuer === user.oidc?.issuer));
  $effect(() => {
    selectedLanguage = user.language ?? '';
  });
  async function load() {
    try {
      auth = await api.request('/api/v1/authentication', authentication);
      error = null;
    } catch (e) {
      error = errorKey(e);
    }
  }
  onMount(() => {
    void load();
  });
  async function save(event: SubmitEvent) {
    event.preventDefault();
    busy = true;
    error = null;
    notice = null;
    try {
      onchanged(
        await api.request('/api/v1/me/profile', account, 'PUT', {
          language: selectedLanguage || null,
          ...(!user.oidc ? { displayName: name } : {}),
        }),
      );
      notice = 'profileSaved';
    } catch (e) {
      error = errorKey(e);
    } finally {
      busy = false;
    }
  }
  async function password(event: SubmitEvent) {
    event.preventDefault();
    error = null;
    notice = null;
    if (newPassword !== confirmation) {
      error = 'passwordMismatch';
      return;
    }
    busy = true;
    try {
      await api.request('/api/v1/me/password', z.object({ status: z.string() }), 'POST', {
        currentPassword,
        newPassword,
      });
      currentPassword = '';
      newPassword = '';
      confirmation = '';
      onchanged(await api.me());
      notice = 'passwordSaved';
    } catch (e) {
      error =
        e instanceof ApiError && e.status === 401
          ? user.hasPassword
            ? 'wrongCurrentPassword'
            : 'recentLoginRequired'
          : errorKey(e);
    } finally {
      busy = false;
    }
  }
</script>

<p>{t('myAccountIntro')}</p>
{#if error}<p class="alert error" role="alert">
    {t(error)}{#if !auth}<button onclick={load}>{t('retry')}</button>{/if}
  </p>{/if}
{#if notice}<p class="alert" role="status">{t(notice)}</p>{/if}
<section class="settings-section">
  <h2>{t('profile')}</h2>
  <form class="settings-form" onsubmit={save}>
    <label
      >{t('displayName')}<input
        bind:value={name}
        required
        maxlength="256"
        readonly={!!user.oidc}
      /></label
    >
    {#if user.oidc}<p class="help">
        {t('managedByProvider')}: {provider?.label ?? user.oidc.issuer}
      </p>{/if}
    <label
      >{t('language')}<select aria-label={t('language')} bind:value={selectedLanguage}
        ><option value="">{t('inheritLanguage')}</option><option value="en">English</option><option
          value="de">Deutsch</option
        ></select
      ></label
    >
    <button class="primary" disabled={busy}>{t('saveProfile')}</button>
  </form>
</section>
<section class="settings-section">
  <h2>{t('signInMethods')}</h2>
  {#if auth}
    {#if user.oidc}<p>
        {provider?.label ?? user.oidc.issuer} · {t(auth.oidc.login ? 'enabled' : 'disabled')}
      </p>{/if}
    <h3>{t('password')}</h3>
    {#if auth.password.login}<form class="settings-form" onsubmit={password}>
        {#if user.hasPassword}<label
            >{t('currentPassword')}<input
              type="password"
              autocomplete="current-password"
              bind:value={currentPassword}
              required
              maxlength="1024"
            /></label
          >{:else}<p class="help">{t('recentLoginRequired')}</p>{/if}
        <label
          >{t('newPassword')}<input
            type="password"
            autocomplete="new-password"
            bind:value={newPassword}
            required
            minlength="12"
            maxlength="1024"
          /></label
        >
        <label
          >{t('confirmPassword')}<input
            type="password"
            autocomplete="new-password"
            bind:value={confirmation}
            required
            minlength="12"
            maxlength="1024"
          /></label
        >
        <button disabled={busy}>{t('savePassword')}</button>
      </form>{:else}<p>{t('passwordDisabled')}</p>{/if}
  {:else}<p role="status">{t('loading')}</p>{/if}
</section>
<section class="settings-section">
  <h2>{t('myConnections')}</h2>
  <Integrations {language} />
</section>
