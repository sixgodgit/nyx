# Email Sending Rules — Iron Law

## Rule
**Always send emails from `REDACTED_EMAIL`. Never use any other email address unless the user explicitly asks.**

## Config
- **SMTP Host**: `mail.privateemail.com`
- **Port**: 587 (STARTTLS)
- **Username**: `REDACTED_EMAIL`
- **Password**: stored in `~/.config/himalaya/config.toml` under `[accounts.enfys]`

## Why
User explicitly stated: "用你自己的邮箱发送邮件" (use your own email to send). The user's Gmail (`REDACTED_EMAIL`) and other addresses are NEVER to be used as the sender unless explicitly requested.

## Recipient
- User's QQ email: `REDACTED_EMAIL` (receive only, never send from)

## Common Mistakes
1. Using `REDACTED_EMAIL` because it's configured in himalaya — WRONG
2. Using `REDACTED_EMAIL` because it's also in credentials — WRONG
3. Forgetting and defaulting to the first working SMTP — WRONG

## Correct Pattern
```python
sender = "REDACTED_EMAIL"
# Use mail.privateemail.com SMTP
```
