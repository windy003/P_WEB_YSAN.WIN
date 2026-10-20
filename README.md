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
- 有用户发帖或发表回应时，通过 QQ 邮箱自动通知站长
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

有用户发帖或发表回应时，系统会向站长邮箱发送一封通知邮件（包含作者、标题、内容摘要和页面链接）。

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

- `MAIL_USERNAME` 和 `MAIL_AUTH_CODE` 都填写后通知才会启用；留空时自动跳过，不影响发帖和回应。
- 默认使用 `smtp.qq.com:465`（SSL）。邮件在后台线程发送，不会拖慢用户发帖。
- 邮件发送失败只会写入服务端日志，不会导致发帖或回应失败。
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
