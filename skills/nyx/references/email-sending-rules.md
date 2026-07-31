# Email Sending Rules — Iron Law

## Rule
**Always send emails from `enfys@hvh.expert`. Never use any other email address unless the user explicitly asks.**

## Config
- **SMTP Host**: `mail.privateemail.com`
- **Port**: 587 (STARTTLS)
- **Username**: `enfys@hvh.expert`
- **Password**: stored in `~/.config/himalaya/config.toml` under `[accounts.enfys]`

## Why
User explicitly stated: "用你自己的邮箱发送邮件" (use your own email to send). The user's Gmail (`talmewhy@gmail.com`) and other addresses are NEVER to be used as the sender unless explicitly requested.

## Recipient
- User's QQ email: `10537543@qq.com` (receive only, never send from)

## Common Mistakes
1. Using `talmewhy@gmail.com` because it's configured in himalaya — WRONG
2. Using `sixgod@hvh.expert` because it's also in credentials — WRONG
3. Forgetting and defaulting to the first working SMTP — WRONG

## Correct Pattern
```python
sender = "enfys@hvh.expert"
# Use mail.privateemail.com SMTP
```
