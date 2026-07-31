# Server & Infrastructure Facts

## Servers

### US Server (小宝/老公)
- **IP**: 162.0.252.252
- **User**: root
- **Password**: kJ7yl60If3C0eBN1Nx
- **Services**: nginx (hvh.expert, visa.hvh.expert), Thalamus (port 9880)

### EU Server — Oracle Amsterdam
- **IP**: 92.5.229.177
- **SSH Key**: `~/.ssh/oracle_eu` (OpenSSH, RSA 2048, SHA256:QbXOgZ8QNKhJyumx35F7WyJHHe71KrVvV6Y2AJ5w7yo)
- **Original PPK**: `/root/.hermes/cache/documents/doc_f37a2dbd2ae3_oracle new.ppk`
- **Status**: SSH port 22 reachable, but banner exchange times out (possible security group IP restriction)
- **Note**: Also referenced as `141.148.226.89` (宝塔面板 at :8888) — may be same/different instance

## Email Accounts

| Email | Purpose | SMTP |
|-------|---------|------|
| **enfys@hvh.expert** | DEFAULT SENDER (always use) | mail.privateemail.com:587 |
| 10537543@qq.com | Receive only | N/A |
| talmewhy@gmail.com | Backup (do NOT use unless asked) | smtp.gmail.com:587 |
| sixgod@hvh.expert | Backup (do NOT use) | mail.privateemail.com:587 |

## Known Issues

### token173.com API Key
- **Status**: BROKEN — stored key is `sk-USh...R4tB` (truncated with literal `...`)
- **Symptom**: API returns "无效的令牌" (invalid token)
- **Impact**: Cannot probe model list; Gemini not visible
- **Fix needed**: User must provide full, untruncated key from token173.com dashboard
