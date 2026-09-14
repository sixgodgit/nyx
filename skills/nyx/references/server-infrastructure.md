# Server & Infrastructure Facts

## Servers

### US Server (小宝/老公)
- **IP**: REDACTED_IP
- **User**: root
- **Password**: REDACTED_PASSWORD
- **Services**: nginx (hvh.expert, visa.hvh.expert), Thalamus (port 9880)

### EU Server — Oracle Amsterdam
- **IP**: REDACTED_IP
- **SSH Key**: `~/.ssh/oracle_eu` (OpenSSH, RSA 2048, REDACTED_SSH_FP)
- **Original PPK**: `REDACTED_PPK_PATH`
- **Status**: SSH port 22 reachable, but banner exchange times out (possible security group IP restriction)
- **Note**: Also referenced as `REDACTED_IP` (宝塔面板 at :8888) — may be same/different instance

## Email Accounts

| Email | Purpose | SMTP |
|-------|---------|------|
| **REDACTED_EMAIL** | DEFAULT SENDER (always use) | mail.privateemail.com:587 |
| REDACTED_EMAIL | Receive only | N/A |
| REDACTED_EMAIL | Backup (do NOT use unless asked) | smtp.gmail.com:587 |
| REDACTED_EMAIL | Backup (do NOT use) | mail.privateemail.com:587 |

## Known Issues

### token173.com API Key
- **Status**: BROKEN — stored key is `REDACTED_API_KEY` (truncated with literal `...`)
- **Symptom**: API returns "无效的令牌" (invalid token)
- **Impact**: Cannot probe model list; Gemini not visible
- **Fix needed**: User must provide full, untruncated key from token173.com dashboard
