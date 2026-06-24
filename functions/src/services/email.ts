/**
 * Email service — swappable behind an interface.
 *
 * Auto-selects the provider at first send: if RESEND_API_KEY is present a real
 * Resend sender is used; otherwise a logging stub (so the emulator and any
 * key-less environment stay log-only with zero code change).
 *
 * For the emulator put RESEND_API_KEY / EMAIL_FROM in functions/.env (or
 * functions/.secret.local). For production set them there too, or bind a Secret
 * Manager secret to the functions.
 */

import { Resend } from "resend";

export interface EmailMessage {
  to: string | string[];
  subject: string;
  text?: string;
  html?: string;
}

export interface EmailService {
  send(msg: EmailMessage): Promise<void>;
}

class StubEmailService implements EmailService {
  async send(msg: EmailMessage): Promise<void> {
    const to = Array.isArray(msg.to) ? msg.to.join(", ") : msg.to;
    console.log(`[email:STUB] -> ${to} | ${msg.subject}`);
  }
}

class ResendEmailService implements EmailService {
  constructor(
    private readonly resend: Resend,
    private readonly from: string,
    private readonly replyTo?: string,
    private readonly bcc?: string
  ) {}

  async send(msg: EmailMessage): Promise<void> {
    const recipients = Array.isArray(msg.to) ? msg.to : [msg.to];
    const to = recipients.filter(Boolean);
    if (to.length === 0) return;
    try {
      await this.resend.emails.send({
        from: this.from,
        to,
        subject: msg.subject,
        text: msg.text ?? msg.subject,
        ...(msg.html ? { html: msg.html } : {}),
        ...(this.replyTo ? { replyTo: this.replyTo } : {}),
        ...(this.bcc ? { bcc: this.bcc } : {}),
      });
    } catch (err) {
      // Never let a transactional email failure break the workflow.
      console.error(`[email:RESEND] failed (${msg.subject}):`, (err as Error).message);
    }
  }
}

let cached: EmailService | null = null;

function service(): EmailService {
  if (cached) return cached;
  const key = process.env.RESEND_API_KEY;
  if (key) {
    cached = new ResendEmailService(
      new Resend(key),
      process.env.EMAIL_FROM || "Media Committee <onboarding@resend.dev>",
      process.env.EMAIL_REPLY_TO || undefined,
      process.env.EMAIL_BCC || undefined
    );
    console.info("[email] using Resend");
  } else {
    cached = new StubEmailService();
  }
  return cached;
}

export const emailService: EmailService = {
  send: (msg) => service().send(msg),
};
