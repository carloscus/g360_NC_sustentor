<script>
  let titulo = 'Mi Proyecto G360';
</script>

<div class="container">
  <header>
    <h1>{titulo}</h1>
  </header>
  <main>
    <slot />
  </main>
</div>

<style>
  .container {
    max-width: 480px;
    margin: 0 auto;
    padding: 16px;
    min-height: 100vh;
    background: var(--g360-bg);
  }

  header {
    padding: 16px 0;
    border-bottom: 1px solid var(--g360-surface);
  }

  h1 {
    margin: 0;
    font-size: 24px;
    font-weight: 700;
    color: var(--g360-accent);
  }

  main {
    padding: 24px 0;
  }
</style>
