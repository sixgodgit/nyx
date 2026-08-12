# 邮件发送配置参考

## 发件邮箱（默认）

| 项目 | 值 |
|------|------|
| 邮箱 | REDACTED_EMAIL |
| SMTP 服务器 | mail.privateemail.com |
| SMTP 端口 | 587 |
| 加密方式 | STARTTLS |
| 密码 | REDACTED_PASSWORD |

## 收件邮箱

| 项目 | 值 |
|------|------|
| QQ 邮箱 | REDACTED_EMAIL |
| 用途 | **仅收件**，不可用于发送 |

## 其他可用邮箱（需用户明确要求才可使用）

| 邮箱 | SMTP 服务器 | 备注 |
|------|-------------|------|
| REDACTED_EMAIL | smtp.gmail.com:587 | Gmail 应用密码 |
| REDACTED_EMAIL | mail.privateemail.com:587 | 密码含中文，SMTP AUTH 可能失败 |

## 铁律

**永远默认使用 REDACTED_EMAIL 发送邮件。**
**永远不要使用 REDACTED_EMAIL 发送邮件。**
**除非用户亲口要求，才能使用其他邮箱。**
