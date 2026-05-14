export default function G360Signature({ name, role, company = 'G360', email, phone }) {
  return `
    <div style="font-family: Arial, sans-serif; color: #333;">
      <strong style="color: #3B82F6;">${name}</strong>
      ${role ? `<span> | ${role}</span>` : ''}
      ${company ? `<span> | ${company}</span>` : ''}
      <br>
      ${email ? `<span>📧 ${email}</span>` : ''}
      ${phone ? `<span> 📱 ${phone}</span>` : ''}
      <hr style="border: 1px solid #3B82F6; margin: 5px 0;">
      <small style="color: #666;">G360 Ecosystem</small>
    </div>
  `;
}
