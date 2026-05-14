/**
 * @file App.jsx
 * @description Componente raíz principal de la aplicación
 * @author @carloscus
 * @version 1.0.0
 */

export default function App() {
  const titulo = 'Mi Proyecto G360';
  
  return (
    <div class="container">
      <header>
        <h1>{titulo}</h1>
      </header>
      <main>
        <slot />
      </main>
    </div>
  );
}
