/**
 * @file app-root.js
 * @description Componente raíz principal de la aplicación
 * @author @carloscus
 * @version 1.0.0
 */

import { LitElement, html, css } from 'lit';

export class AppRoot extends LitElement {
  static properties = {
    titulo: { type: String },
    tema: { type: String },
  };

  static styles = css`
    :host {
      display: block;
      min-height: 100vh;
      --g360-bg: #0b1220;
      --g360-surface: #151e2e;
      --g360-accent: #00d084;
      --g360-text: #f0f4f8;
      --g360-muted: #94a3b8;
    }

    .container {
      max-width: 480px;
      margin: 0 auto;
      padding: 16px;
      min-height: 100vh;
      background: var(--g360-bg);
      color: var(--g360-text);
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
  `;

  constructor() {
    super();
    this.titulo = 'Mi Proyecto G360';
    this.tema = 'dark';
  }

  render() {
    return html`
      <div class="container">
        <header>
          <h1>${this.titulo}</h1>
        </header>
        <main>
          <slot></slot>
        </main>
      </div>
    `;
  }
}

customElements.define('app-root', AppRoot);
