<script lang="ts">
  import '../app.css';
  let { children } = $props();
</script>

<svelte:head
  ><title>Family Controls</title><meta name="robots" content="noindex, nofollow" /></svelte:head
>
{@render children()}
