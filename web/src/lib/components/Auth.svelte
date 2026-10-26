<script lang="ts">
  import { z } from 'zod';
  import { Api, ApiError, errorKey } from '../api';
  import { account, type WebConfig } from '../contracts';
  import { translate, type Language, type Message } from '../i18n';
  let {
    config,
    language,
    onlogin,
  }: { config: WebConfig; language: Language; onlogin: () => Promise<void> } = $props();
  let registering = $state(false),
    username = $state(''),
    password = $state(''),
    invitation = $state('');
  let busy = $state(false),
    error = $state<Message | null>(null),
    notice = $state<Message | null>(null);
  const api = new Api();
  const t = (key: Message) => translate(language, key);
  const mode = $derived(registering || (!config.password.login && config.password.registration));
  async function submit(event: SubmitEvent) {
    event.preventDefault();
    busy = true;
    error = null;
    notice = null;
    try {
      if (mode) {
        const result = await api.request('/api/v1/auth/register', account, 'POST', {
          username,
          password,
          ...(invitation ? { invitation } : {}),
        });
        notice = result.status === 'pending' ? 'approvalPending' : 'registered';
        registering = false;
      } else {
        await api.request('/api/v1/auth/login', z.object({ expiresIn: z.number() }), 'POST', {
          username,
          password,
        });
        await onlogin();
      }
      password = '';
    } catch (e) {
      error = e instanceof ApiError && e.status === 401 ? 'authFailed' : errorKey(e);
    } finally {
      busy = false;
    }
  }
</script>

<div class="auth-layout">
  <section class="auth-intro">
    <h1>Family Controls</h1>
    <h2>{t('welcome')}</h2>
    <p>{t('loginIntro')}</p>
  </section>
  <section class="auth-form" aria-labelledby="sign-in-title">
    <h2 id="sign-in-title">{t(mode ? 'register' : 'signin')}</h2>
    {#if error}<p class="alert error" role="alert">{t(error)}</p>{/if}
    {#if notice}<p class="alert" role="status">{t(notice)}</p>{/if}
    {#if config.password.login || config.password.registration}
      <form onsubmit={submit}>
        <label
          >{t('username')}<input
            bind:value={username}
            autocomplete="username"
            required
            maxlength="64"
            pattern="[a-zA-Z0-9_][a-zA-Z0-9_.\-]*"
          /></label
        >
        <label
          >{t('password')}<input
            type="password"
            bind:value={password}
            autocomplete={mode ? 'new-password' : 'current-password'}
            required
            minlength={mode ? 12 : undefined}
            maxlength="1024"
          /></label
        >
        {#if mode}<p class="help">{t('passwordMin')}</p>{/if}
        {#if mode || config.oidc.registration}<label
            >{t('invitation')}<input
              bind:value={invitation}
              maxlength="256"
              autocomplete="off"
            /></label
          >{/if}
        <button class="primary" disabled={busy}
          >{t(busy ? 'sending' : mode ? 'register' : 'signin')}</button
        >
      </form>
      {#if config.password.login && config.password.registration}<button
          class="text-button"
          onclick={() => {
            registering = !registering;
            error = null;
            notice = null;
          }}>{t(mode ? 'backToLogin' : 'register')}</button
        >{/if}
    {/if}
    {#if config.oidc.login || config.oidc.registration}
      {#if !(config.password.login || config.password.registration) && config.oidc.registration}<label
          >{t('invitation')}<input
            bind:value={invitation}
            autocomplete="off"
            maxlength="256"
          /></label
        >{/if}
      <div class="provider-list">
        {#each config.providers as provider}<a
            class="button"
            data-sveltekit-reload
            href={`/api/v1/auth/oidc/${encodeURIComponent(provider.id)}/start${invitation ? `?invitation=${encodeURIComponent(invitation)}` : ''}`}
            >{t('withProvider')} {provider.label}</a
          >{/each}
      </div>
    {/if}
    {#if !config.password.login && !config.password.registration && (!(config.oidc.login || config.oidc.registration) || !config.providers.length)}<p
      >
        {t('disabledLogin')}
      </p>{/if}
  </section>
</div>
