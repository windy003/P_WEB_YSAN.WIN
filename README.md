# 耶稣爱你.胜利

一个使用 Flask 构建的中文社区网站。顶部横幅使用 `static/banner.png`，游客可以浏览讨论，注册用户可以发起主题、发表回应。

## 功能

- 顶部横幅展示 `static/banner.png`
- 注册账号（用户名 + 中国大陆手机号 + 密码）
- 双方式登录：
  - 手机号 + 阿里云短信验证码（公共签名、公共模板）；未注册手机号验证成功后自动创建账号并登录
  - 用户名 + 密码（用户名不区分大小写）
- 登录后发帖、发表回应
- 管理后台：访问 `/admin`，使用 `.env` 中配置的管理员账号登录，查看站点统计、最新帖子和回应
- 作者可以重新编辑自己发布的帖子（标题、正文，并可追加图片或视频），编辑按钮仅作者可见
- 用户之间可以一对一私信，聊天记录保留 7 天后自动清除
- 有用户发帖、修改帖子或发表回应时，通过 QQ 邮箱自动通知站长
- 点击顶栏头像进入个人资料，可上传或更换头像、设置昵称；登录仍使用原用户名
- 发帖支持上传图片和视频，文件保存在 `instance/media`
- 游客可浏览首页和讨论详情
- SQLite 持久化数据，密码使用 Werkzeug 安全哈希保存
- 表单 CSRF 防护、登录后会话刷新、安全的登录跳转
- 短信验证码 60 秒重发限制、5 分钟有效期、失败尝试限制
- 响应式布局，适配桌面与手机

## 本地运行

需要 Python 3.10 及以上版本。

```powershell
# 在项目目录中
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python app.py
```

打开 <http://127.0.0.1:5000>，点击“加入社区”创建账号即可开始使用。

如果 PowerShell 阻止激活脚本，也可以不激活虚拟环境，直接使用虚拟环境中的 Python：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe app.py
```

## 开启调试模式

```powershell
$env:FLASK_DEBUG = "1"
python app.py
```

## 阿里云短信配置

手机号登录使用阿里云「号码认证服务 → 短信认证」，通过公共签名和公共模板发送验证码。先在阿里云控制台开通短信认证服务，并准备一个具备该服务调用权限的 RAM AccessKey。

编辑项目根目录的 `.env` 文件，填写 AccessKey：

```dotenv
ALIYUN_ACCESS_KEY_ID=你的AccessKeyID
ALIYUN_ACCESS_KEY_SECRET=你的AccessKeySecret

# 公共签名与公共模板
ALIYUN_SMS_SIGN_NAME=速通互联验证码
ALIYUN_SMS_TEMPLATE_CODE=100001

ALIYUN_SMS_REGION_ID=cn-hangzhou
ALIYUN_SMS_ENDPOINT=https://dypnsapi.aliyuncs.com/
```

说明：

- `.env` 已在 `.gitignore` 中，不会被提交到 Git。可用 `.env.example` 作为模板。
- 修改 `.env` 后需要重启应用才会生效。
- 公共签名可能由阿里云不定期调整，如发送失败，请在短信认证控制台核对当前赠送的签名与模板，并更新 `.env`。
- 系统调用的接口为 `SendSmsVerifyCode` 和 `CheckSmsVerifyCode`，验证码由阿里云生成与校验。
- 未注册手机号通过验证后会自动创建账号，用户名形如“新朋友1234”。
- 同一手机号 60 秒内只能获取一次验证码，1 小时最多 5 次；验证码 5 分钟内有效，最多尝试 5 次。
- AccessKey 只保存在服务端，不会发送到浏览器。

官方文档：
- [短信认证服务](https://help.aliyun.com/zh/pnvs/user-guide/sms-authentication-service)
- [SendSmsVerifyCode](https://help.aliyun.com/zh/pnvs/developer-reference/api-dypnsapi-2017-05-25-sendsmsverifycode)
- [CheckSmsVerifyCode](https://help.aliyun.com/zh/pnvs/developer-reference/api-dypnsapi-2017-05-25-checksmsverifycode)

## QQ 邮箱通知

有用户发帖、修改帖子或发表回应时，系统会向站长邮箱发送一封通知邮件（包含作者、标题、内容摘要和页面链接）。

### 获取 QQ 邮箱授权码

1. 登录 QQ 邮箱，进入「设置 → 账户」。
2. 找到「POP3/IMAP/SMTP/Exchange/CardDAV/CalDAV 服务」，开启「IMAP/SMTP 服务」。
3. 按提示生成「授权码」。这个授权码不是 QQ 密码，请妥善保存。

### 在 `.env` 中配置

```dotenv
MAIL_USERNAME=12345678@qq.com
MAIL_AUTH_CODE=你的SMTP授权码
# 接收通知的邮箱；留空则发送到 MAIL_USERNAME
MAIL_TO=12345678@qq.com
MAIL_HOST=smtp.qq.com
MAIL_PORT=465
MAIL_SENDER_NAME=耶稣爱你.胜利
```

- `MAIL_USERNAME` 和 `MAIL_AUTH_CODE` 都填写后通知才会启用；留空时自动跳过，不影响发帖、修改帖子和回应。
- 默认使用 `smtp.qq.com:465`（SSL）。邮件在后台线程发送，不会拖慢用户发帖或修改帖子。
- 邮件发送失败只会写入服务端日志，不会导致发帖、修改帖子或回应失败。
- 修改 `.env` 后需要重启应用才会生效。

## 管理后台

在 `.env` 中配置管理员账号：

```dotenv
admin_name=你的管理员用户名
admin_password=你的管理员密码
```

然后访问 <http://127.0.0.1:5012/admin> 登录。

- 也支持 `ADMIN_USERNAME` / `ADMIN_PASSWORD` 环境变量。
- 未配置管理员账号时，管理后台不会开放登录。
- 登录后可查看注册用户数、帖子数、回应数，以及最新的帖子和回应。
- 管理员登录状态与普通用户登录相互独立；点击“退出管理后台”可单独退出。
- 登录表单与退出操作均受 CSRF 保护。

## 图片与视频

发帖时可以同时选择图片和视频：

- 支持 JPG、PNG、GIF、WebP、MP4、M4V、MOV、WebM、OGV
- 每条帖子最多 6 个附件
- 单张图片最大 10 MB，单个视频最大 300 MB，总大小最大 320 MB
- 文件会使用随机文件名保存在 `instance/media`
- 服务端会校验扩展名与文件头，拒绝伪装文件
- 图片和视频仅在所属帖子页面公开展示

## 头像与昵称

点击顶栏头像即可进入个人资料页：

- 头像支持 JPG、PNG、GIF、WebP，最大 5 MB
- 头像文件使用随机文件名保存在 `instance/avatars`
- 昵称为 2–20 个字符，支持中文、英文、数字和下划线，且不能与其他用户的用户名或昵称重复
- 昵称用于帖子和回应中的显示；留空则显示用户名
- 修改昵称不会影响登录：用户名 + 密码登录仍然使用原用户名
- 点击帖子或回应中的头像会进入该用户的公开个人主页（`/users/<id>`），可查看其昵称、介绍与帖子
- 个人资料页和用户个人主页的头像支持点击放大查看

## 私信

- 在别人的个人主页点击「私信」即可开始一对一聊天
- 顶栏「私信」入口展示全部会话和未读数量；手机端显示为私信图标
- 每条私信最多 1000 个字符，只有会话双方可以看到聊天内容
- 聊天记录只保留最近 7 天：超过 7 天的消息会在访问私信页面时清除，站点也会每小时自动清理一次
- 打开会话后，对方发来的未读消息会自动标记为已读

## 数据与配置

- 数据库：首次启动时自动创建于 `instance/community.db`
- 媒体文件：保存在 `instance/media`
- 头像文件：保存在 `instance/avatars`
- 本地会话密钥：首次启动时自动生成于 `instance/.secret_key`
- 短信及站点配置从 `.env` 和环境变量读取
- 如需指定密钥或端口，可通过环境变量设置：

| 环境变量 | 说明 | 默认值 |
| --- | --- | --- |
| `SECRET_KEY` | Flask 会话密钥 | 自动生成并保存在 `instance/.secret_key` |
| `HOST` | 监听地址 | `127.0.0.1` |
| `PORT` | 监听端口 | `5000` |
| `FLASK_DEBUG` | 设为 `1` 开启调试 | 关闭 |
| `SESSION_COOKIE_SECURE` | HTTPS 部署时设为 `1` | 关闭 |

> 生产部署时请通过环境变量设置一个足够随机的 `SECRET_KEY`，关闭调试模式，并使用 HTTPS 与 `SESSION_COOKIE_SECURE=1`。开发服务器不适合直接暴露到公网，可使用 Waitress（Windows）或 Gunicorn（Linux）等 WSGI 服务器。

### 访客 IP 与 Cloudflare

应用默认只监听 `127.0.0.1`，前面通常还有一层本机反向代理（Nginx 或 cloudflared 隧道），再往前是 Cloudflare 的橙色云代理。因此直接读取 `request.remote_addr` 只会得到 `127.0.0.1`，后台的「24 小时独立 IP」会被压缩成 1 个。

后台统计和短信频控现在通过 `get_client_ip()` 获取真实 IP，优先级为：

1. `CF-Connecting-IP`（Cloudflare 会覆盖该请求头，最可靠）
2. `X-Real-IP`
3. `X-Forwarded-For` 的最右侧一项
4. `REMOTE_ADDR`

只有当直连对端是回环或内网地址（即本机反向代理）时才会信任上述请求头，避免源站被公网直连时伪造 IP。

后台「独立 IP」列表和详情页还会显示该 IP 最近一次登录访问的用户名（昵称 + `@用户名`）；未登录访问显示「未登录访问」。数据库会在启动时自动为 `visitors` 表补充 `user_id` 字段，无需手动迁移。

### Caddy 反向代理

Caddy 的 `reverse_proxy` 默认会把 `CF-Connecting-IP` 原样转发给上游，并自动设置 `X-Forwarded-For`，因此无需为真实 IP 做额外配置，最基本的反代即可：

```caddyfile
ysan.win {
    reverse_proxy 127.0.0.1:5012
}
```

如果希望 Caddy 自己的访问日志和 `{http.request.remote.host}` 也显示真实访客 IP（而不是 Cloudflare 边缘节点），需要在全局配置里信任 Cloudflare 的 IP 段。Caddy 会据此从 Cloudflare 写入的 `X-Forwarded-For` 中解析出客户端地址：

```caddyfile
{
    servers {
        trusted_proxies static 173.245.48.0/20 103.21.244.0/22 103.22.200.0/22 103.31.4.0/22 141.101.64.0/18 108.162.192.0/18 190.93.240.0/20 188.114.96.0/20 197.234.240.0/22 198.41.128.0/17 162.158.0.0/15 104.16.0.0/13 104.24.0.0/14 172.64.0.0/13 131.0.72.0/22 2400:cb00::/32 2606:4700::/32 2803:f800::/32 2405:b500::/32 2405:8100::/32 2a06:98c0::/29 2c0f:f248::/32
        trusted_proxies_strict
    }
}

ysan.win {
    reverse_proxy 127.0.0.1:5012
}
```

Cloudflare 会覆盖每个经它代理的请求里的 `CF-Connecting-IP`，所以应用读取该请求头不会被访客伪造；前提是源站不能绕过 Cloudflare 被公网直接访问（只监听本机或防火墙只放行 Cloudflare 网段即可）。

升级前已经记录的 `127.0.0.1` 访客记录不会自动消失，可在后台数据库执行以下语句清理：

```sql
DELETE FROM visitors WHERE ip_address = '127.0.0.1';
```

## 运行测试

```powershell
python -m unittest discover -s tests -v
```

## 项目结构

```text
.
├── app.py                  # Flask 应用、数据模型与路由
├── .env                    # 本地配置（含 AccessKey，不提交）
├── .env.example            # 配置模板
├── requirements.txt
├── templates/
│   ├── base.html
│   ├── _macros.html
│   ├── index.html
│   ├── profile.html        # 个人资料（头像与昵称）
│   ├── user_profile.html   # 用户公开个人主页
│   ├── messages.html       # 私信会话列表
│   ├── message_thread.html # 私信聊天页面
│   ├── post_detail.html
│   ├── post_new.html
│   ├── post_edit.html      # 编辑自己的帖子
│   ├── error.html
│   ├── auth/
│   │   ├── login.html
│   │   └── register.html
│   └── admin/
│       ├── login.html     # 管理员登录
│       └── dashboard.html # 管理后台
├── static/
│   ├── banner.png          # 顶部横幅图片
│   ├── css/style.css
│   ├── js/app.js
│   └── favicon.svg
└── tests/test_app.py
```
