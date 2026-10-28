<script lang="ts">
  import { onMount } from 'svelte';
  import { goto } from '$app/navigation';
  import { z } from 'zod';
  import { Api, errorKey } from '#lib/api.ts';
  import { translate, type Language, type Message } from '#lib/i18n.ts';
  let language = $state<Language>('en');
  let message = $state<Message>('oidcProgress');
  onMount(() => {
    try {
      language = localStorage.getItem('family.language') === 'de' ? 'de' : 'en';
    } catch {
      // Browser storage is optional; the sign-in itself uses an HttpOnly cookie.
      language = navigator.language.startsWith('de') ? 'de' : 'en';
    }
    document.documentElement.lang = language;
    const url = new URL(location.href);
    // Clear authorization codes from the visible history before any asynchronous work.
    history.replaceState(null, '', url.pathname);
    if (!url.searchParams.has('code') || !url.searchParams.has('state')) {
      message = 'authFailed';
      return;
    }
    const api = new Api();
    void api
      .request(
        url.pathname + url.search,
        z.object({ status: z.string().optional(), expiresIn: z.number().optional() }),
      )
      .then(async (result) => {
        if (result.status === 'pending') message = 'approvalPending';
        else if (result.expiresIn) await goto('/', { replaceState: true });
        else message = 'registered';
      })
      .catch((error) => {
        message = errorKey(error);
      });
  });
</script>

<main class="callback">
  <h1>Family Controls</h1>
  <p role="status">{translate(language, message)}</p>
  <a href="/">{translate(language, 'backToLogin')}</a>
</main>
