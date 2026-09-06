# 邮件域名认证 DNS 配置说明(SPF / DKIM / DMARC / rDNS)

> 适用:自建邮件域 / AIMail 独立域名系统 / 任何 SMTP 直发的邮件服务器。
> 接收方(QQ 邮箱、Gmail、Outlook 等)信任一封来自陌生服务器的邮件,
> 靠的就是域名上的这四组 DNS 记录。缺一组,邮件就可能进垃圾箱甚至被拒收。
>
> 🇬🇧 [English](./email-dns-auth.md)

## 0. 总览

| 记录 | 在哪设 | 作用 | 是否必须 |
|---|---|---|---|
| SPF | 发件域 TXT | 声明"哪些服务器有权以本域发信" | 强烈建议 |
| DKIM | `{selector}._domainkey` TXT | 公布签名公钥,接收方验证邮件未被篡改 | 建议(直发必须) |
| DMARC | `_dmarc` TXT | 告诉接收方 SPF/DKIM 都失败时怎么处理,并收报告 | 建议 |
| rDNS/PTR | 服务器 IP 的反向区 | 让 IP 反查回域名(部分接收方/反垃圾用) | 视接收方 |

## 1. SPF(发件域授权)

**记录位置**:发件域本身(子域发件就在子域;根域发件在根域)。多数面板
"主机记录"填 `@` 或留空(表示域本身)。

**记录值**(常用示例):
```
v=spf1 mx ~all          # 域名的 MX 服务器可代发(softfail 其它)
v=spf1 ip4:203.0.113.10 -all   # 仅指定 IP 可发(-all = 其它一律拒绝)
```

**参数逐项**:
| 部分 | 含义 |
|---|---|
| `v=spf1` | 版本标记,固定开头 |
| `mx` | 该域的 MX 记录指向的服务器可发信 |
| `ip4:203.0.113.10` | 允许该 IPv4 发信(可多个;`ip6:` 同理) |
| `include:_spf.example.com` | 引用另一域名的 SPF 策略(如企业邮/邮件服务商) |
| `~all` | 不在名单的发信:softfail(收但不标记强) |
| `-all` | 不在名单的发信:fail(建议接收方拒收;先 `~all` 稳定后再改) |

注意:SPF 有 10 次 DNS 查询上限,`include` 链别太长。
验证:`dig +short TXT 你的域名` 应看到 `v=spf1 …`。

## 2. DKIM(签名公钥)

DKIM 由三件套组成:**私钥签名(服务器侧)+ 公钥发布(DNS)+ selector(名字)**。
收件服务器按邮件里的 `d=域名`、`s=selector` 去查对应 TXT,用公钥验签。

**密钥生成与公钥提取**(服务器上执行):
```bash
openssl genrsa -out /path/dkim/{域名}.pem 2048      # 私钥,服务器保存
chmod 600 /path/dkim/{域名}.pem
openssl rsa -in /path/dkim/{域名}.pem -pubout -outform DER | openssl base64 -A   # 得到公钥串
```

**记录位置**:主机记录填 `{selector}._domainkey`(selector 由服务器配置,
如 `aimail` → `aimail._domainkey`)。

**记录值**:
```
v=DKIM1; k=rsa; p=<上一步输出的公钥整串>
```

**参数逐项**:
| 部分 | 含义 |
|---|---|
| `v=DKIM1` | 版本,固定 |
| `k=rsa` | 密钥算法(可省略,默认 rsa) |
| `h=sha256` | 签名哈希(部分服务商自动加,可省略) |
| `p=…` | **公钥本体**(一整串 base64,不要换行、不要截断) |

验证:`dig +short TXT {selector}._domainkey.{域名}` 应返回以
`v=DKIM1` 开头的记录,`p=` 值与服务器公钥一致。

## 3. DMARC(收件方策略与报告)

**记录位置**:主机记录填 `_dmarc`(固定)。

**记录值**(监控起步,稳定后加严):
```
v=DMARC1; p=none; rua=mailto:admin@example.com
v=DMARC1; p=quarantine; rua=mailto:admin@example.com; pct=100
```

**参数逐项**:
| 部分 | 含义 |
|---|---|
| `v=DMARC1` | 版本,固定 |
| `p=none` | 策略:仅监控(不拒收;建议先用它观察报告) |
| `p=quarantine` | SPF/DKIM 都失败 → 进垃圾箱 |
| `p=reject` | SPF/DKIM 都失败 → 拒收(最严,需先确认无误配) |
| `rua=mailto:…` | 聚合报告收件地址(可多个,逗号分隔) |
| `pct=100` | 策略应用比例(先小比例试点可填 10) |
| `sp=` | 子域单独策略(可选) |

注意:DMARC 需要 SPF **或** DKIM 至少一项通过且域对齐才算 pass——
所以 DMARC 是前两项的"裁判",前两项不配它没有意义。
验证:`dig +short TXT _dmarc.{域名}`。

## 4. rDNS / PTR(服务器 IP 反解)

**位置**:服务器公网 IP 的反向 DNS(在云服务商/IDC 的"反向解析/PTR"
设置面板操作,不在域名解析商)。

**设置**:把 `IP` 的反查结果设为发信域名(如 `mail.example.com`),且该
域名有对应的 A 记录指回此 IP。SMTP banner/EHLO 名建议一致。

**作用**:多数接收方不做硬校验,但 Gmail/Outlook 等对"IP 反查无记录
或与发信域无关"的服务器会降低信任。验证:
```bash
dig +short -x <服务器IP>        # 应返回你的发信域名
```

## 5. 配置完成后自检

```bash
# SPF
dig +short TXT example.com
# DKIM
dig +short TXT aimail._domainkey.example.com
# DMARC
dig +short TXT _dmarc.example.com
# rDNS
dig +short -x 203.0.113.10
# 真实发信验证:给 Gmail/QQ 发一封,查看原始邮件头:
#   Authentication-Results: … spf=pass … dkim=pass … dmarc=pass
```

## 6. 排查速查

| 现象 | 可能原因 |
|---|---|
| 邮件进垃圾箱 | SPF/DKIM/DMARC 缺一;PTR 无记录;内容触发 |
| 头显示 `spf=fail` | 发信服务器不在 SPF 名单(加 `ip4:`/`mx`) |
| 头显示 `dkim=fail` | `p=` 公钥与服务器私钥不匹配 / selector 名不一致 / 记录被截断 |
| 头显示 `dmarc=fail` | SPF、DKIM 全失败或域不对齐 |
