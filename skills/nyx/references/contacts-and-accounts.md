# 关键联系人 & 账户速查

> 这是 Nyx 的高频检索目标，列在这里是为了加速常见查询。
> 每次涉及这些信息时，**仍然必须走 Nyx 检索流程**——此表仅作为 skill 内部的快速参考。

## 邮箱账户

| 角色 | 地址 | 用途 |
|------|------|------|
| 主人收件 | REDACTED_EMAIL | 只收件，禁止发件 |
| 默认发件 | REDACTED_EMAIL | 主要发件人 |
| 备选发件 | REDACTED_EMAIL | SMTP AUTH 可能失败（中文密码） |
| 备选发件 | REDACTED_EMAIL | Gmail 应用密码 |

## 服务器

| 名称 | IP | 位置 | 凭据 |
|------|-----|------|------|
| 美服（小宝/老公） | REDACTED_IP | 🇺🇸 | root / REDACTED_PASSWORD |
| 荷兰 Oracle | REDACTED_IP | 🇳🇱 阿姆斯特丹 | 未存储 |
| 阿姆斯特丹宝塔面板 | REDACTED_IP | 🇳🇱 | 未存储 |

## 邮件发送规则

1. **默认发件人**：REDACTED_EMAIL
2. **收件人**：REDACTED_EMAIL（主人）
3. **SMTP**：mail.privateemail.com:587 (STARTTLS)
4. **附件**：必须显式设置 Content-Type 和 UTF-8 filename
5. **发送前**：必须通过 Nyx 检索确认收件人地址
