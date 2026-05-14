export default function G360DragModal({ isOpen, onClose, title, children }) {
  if (!isOpen) return null;
  
  return `
    <div class="g360-modal-overlay" style="
      position: fixed;
      top: 0;
      left: 0;
      right: 0;
      bottom: 0;
      background: rgba(0,0,0,0.5);
      display: flex;
      align-items: center;
      justify-content: center;
      z-index: 1000;
    ">
      <div class="g360-modal" style="
        background: white;
        border-radius: 8px;
        box-shadow: 0 4px 20px rgba(59, 130, 246, 0.3);
        min-width: 400px;
        max-width: 90vw;
      ">
        <div class="g360-modal-header" style="
          background: #3B82F6;
          color: white;
          padding: 15px 20px;
          border-radius: 8px 8px 0 0;
          cursor: move;
        ">
          <span>${title}</span>
          <button onclick="(${onClose})()" style="float: right; background: none; border: none; color: white; cursor: pointer;">✕</button>
        </div>
        <div class="g360-modal-body" style="padding: 20px;">
          ${children}
        </div>
      </div>
    </div>
  `;
}
