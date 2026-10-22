# Docker 与自托管部署

本 fork 基于 `LCX-luo/GoodnightUESTC`，只维护 Docker / Web 部署，正式入口为 `server.py`。学校登录使用独立 Chromium，不需要服务器桌面或 VNC；成员只需访问网页。

## 架构与保存范围

公网请求经过 Caddy 或 Nginx 的 HTTPS 后进入后端，页面不要求额外的网站账号或 HTTP Basic Auth。用户直接完成学校统一认证；学校相关操作要求有效学校会话，本站不另设测试组白名单，任何具备相应学校账号与业务权限的人均可使用。后端校验内部代理密钥、公开 Host、Origin 和 CSRF，并按浏览器及来源 IP 限速。用户只需浏览器，无需在本机运行 Python。`hosted.py` 管理隔离的工作台与资源，`session_packages.py` 负责加密会话包、设备绑定与恢复许可。

`GATEWAY_SECRET` 是反向代理与后端之间的内部共享密钥，由运维配置，用户无需输入；它不承担成员登录功能。代理覆盖 `X-Team-Gateway`、`X-Client-IP` 并删除旧 `X-Team-User` 和 Authorization 头。后端仅在密钥校验通过后信任代理提供的来源 IP，不使用客户端自报的身份或 `X-Forwarded-For`。默认部署要求代理直接接收公网连接。

1. 学号、密码、验证码通过 HTTPS 发到后端，在独立 Chromium context 中完成学校登录；密码仅供本次登录，不保存用于自动重登。
2. 登录成功后关闭 Chromium，取得学校 Cookie、学号、User-Agent 和保存时间的快照。默认开启“在此浏览器记住学校登录”，可随时关闭；共用设备请关闭。
3. 后端以 AES-256-GCM 加密快照，绑定当前公开站点及随机的 HttpOnly 浏览器设备 Cookie。网页只收到加密包，不会收到明文学校 Cookie；签名不等于加密，本实现不把快照塞进 Flask 签名 Cookie。
4. 加密包由用户浏览器保存。后端工作台被清理或容器更新后，网页上传包，后端验证完整性、期限、设备绑定与恢复许可，再执行一次有超时的学校只读请求。学校仍认可会话时恢复；该过程不启动 Chromium，不提交账号密码或签到。
5. 后续学校请求各使用独立 CookieJar；学校返回的 Cookie 更新／删除会更新当前快照，前端在会话版本变化时刷新本机包。轮询和恢复不延长包的绝对期限。

每个浏览器工作台拥有独立的学校会话、操作锁、进度、CSRF 和确认令牌；同一浏览器的普通标签页通常共享工作台。不同浏览器即使共用来源 IP，也有独立随机设备标识，不能直接复制包恢复，因为缺少原浏览器的 HttpOnly 设备 Cookie。仅复制工作台 Cookie 也不能接管另一个设备的工作台。设备标识不代表学校身份，学校身份由学校认证确定；来源 IP 仅限速，不参与包绑定，切换网络不要求重新登录。位置、待确认操作和学校登录画面不包含在加密包中；工作台重建后必须重新选择并确认实际位置。

“浏览器保存”不表示凭据不经过后端：学校 Cookie 在服务器端产生，使用时也在服务器内存中解密。HTTPS 终止代理和后端都必须可信。加密包仍按敏感凭据保护，禁止写入日志或分享；同源脚本虽看不到包内明文，仍可能调用本站恢复接口，AES 加密不能替代 XSS 防护。

`attempts` 持久卷现在包含以下文件，**没有学校 Cookie 快照或密码数据库**：

| 文件 | 内容与用途 |
| --- | --- |
| `attempts.sqlite3` | 账号 SHA-256、业务日期、提交尝试结果和时间，用于跨重启防重复 POST |
| `session-package.key` | 独立的 32 字节加密密钥，首次启动自动生成，文件权限 0600；与网关密钥分开 |
| `session-packages.sqlite3` | 浏览器设备的 HMAC 标识、许可版本命名空间、随机恢复许可 ID、签发与到期时间；不含学校密码、Cookie、位置或完整学校记录 |

恢复许可用于撤销已复制的旧包，默认全站最多 1024 个、同一浏览器设备只保留一个有效许可；同一设备的新登录替换旧许可，过期行在签发新包时回收。卷内 SQLite 可能产生事务临时文件，应整体保护。加密密钥和数据库必须一起保留、一起备份；丢失密钥不能解密，丢失许可数据库会使原包无法恢复。检测到“许可数据库存在但密钥丢失”时服务拒绝启动，不会静默生成新密钥。

Caddy 的 `caddy_data` / `caddy_config` 用于证书和入口配置。Chromium 临时 profile、缓存和临时缓冲文件位于 `/tmp` tmpfs。后端使用非 root、只读根目录、默认 Docker seccomp、去除 capabilities 和禁止提权。Playwright 默认关闭 Chromium 自身 sandbox，隔离依赖容器限制与学校域名白名单；不要挂载 Docker socket。

<a id="sessions"></a>

## 登录状态与有效期

**默认开启记住登录后，今天登录、明天用同一浏览器打开，可以自动尝试恢复，不再因后端内存清理就要求重登。** 学校会话是否仍有效由学校决定，不能保证一定可跨日。未开启保存或浏览器拒绝保存时，只有当前内存工作台有效。

| 期限／条件 | 当前默认行为 |
| --- | --- |
| 浏览器加密包 | `SESSION_PACKAGE_TTL_SECONDS=604800`，从首次签发最多 7 天；恢复、读取和 Cookie 更新均不延期，重新完成学校登录并保存才签发新期限 |
| 工作台空闲 | `SESSION_IDLE_SECONDS=1800`，30 分钟没有请求后清理服务器内存；恢复时新建工作台，不要求重新输入学校密码 |
| 工作台绝对期限 | `SESSION_LIFETIME_SECONDS=28800`，从工作台创建最多 8 小时；首次访问即可能创建。到期可恢复加密包，这不是学校 Cookie 的有效期 |
| 学校会话 | 学校 Cookie 到期和学校服务端策略可能提前失效；学校明确拒绝时撤销当前设备恢复许可并清除本机保存，要求重登 |
| 浏览器保存条件 | 需保留站点本地存储与设备 Cookie；清站点数据、结束无痕会话、换设备／浏览器或更换域名，无法自动恢复 |
| 后端条件 | 重启和更新允许恢复，前提是保留同一密钥、许可数据库和公开域名；无需保留旧 Flask 工作台 Cookie |

例如，今天 21:10 关闭网页，明天 21:00 打开，旧内存工作台已经清理；网页读取本机包并请求恢复。包仍在 7 天期限内且学校验证通过，即可继续使用，重新确认位置后再进行单次操作。跨午夜本身不触发失效。

恢复遇到网络超时或学校暂时不可达时，保留本机包，展示“重试保存或恢复”，不会把网络失败当作登录失效，也不会每次轮询都重试学校请求。每个工作台自动尝试一次；刷新／重建工作台或本人点击重试可再次尝试。后端按浏览器标识限制恢复请求为每 5 分钟 5 次，并另设来源 IP 预算。保存失败不影响当前登录的单次使用，页面明确提示未保存成功。

| 内容 | 用户浏览器 | 服务器 |
| --- | --- | --- |
| 学校密码与验证码 | 输入并经 HTTPS 发送，发起 Docker 登录后清空密码框 | 临时处理，不放入包或业务日志，不用于次日自动重登 |
| 学校 Cookie / 学号 / User-Agent | 仅保存 AES-GCM 加密包 | 当前用户的隔离内存，使用时解密并代发学校请求 |
| `__Host-uestc` | Secure / HttpOnly / SameSite=Strict 的浏览器会话 Cookie，只有签名随机工作台 ID | 随进程启动随机生成签名密钥，重启后重新建立工作台 |
| `__Host-uestc-device` | Secure / HttpOnly / SameSite=Strict 的持久 Cookie，只有随机设备标识，期限与保存配置相同 | 用于加密绑定；导出／恢复时续上设备 Cookie，但不延长包期限 |

设备 Cookie 只是随机标识，拿到它不能单独恢复学校会话；还需要匹配的加密包和有效恢复许可。浏览器会话恢复可能保留 `__Host-uestc`，不能把关闭窗口当作注销。

“清除学校会话与本机保存”清除当前学校内存和该设备本地包，并在服务器撤销恢复许可；保留的旧包不能再恢复。取消“记住学校登录”清除本机包、撤销许可，但允许当前内存工作台继续使用。该操作不注销学校其他设备。位置与提交前确认仍按每次操作处理。

## 浏览器兼容与资源占用

前端优先使用 **IndexedDB**，打开或事务失败／被阻止／超时后尝试 **localStorage**。只保存加密包、期限和保存偏好，不依赖浏览器 Web Crypto、Service Worker、PWA 安装或持久存储权限弹窗。写入成功后才显示保存成功。localStorage 只用于少量数据与跨标签页通知；包不会每次轮询重复读写。

| 浏览器／模式 | 行为与限制 |
| --- | --- |
| 当前版本 Chrome / Edge / Firefox / Safari（桌面与移动端） | 均支持 IndexedDB、localStorage 和本站所需 Cookie；实际可用性取决于站点存储策略。工程未承诺逐版本兼容 |
| IndexedDB 被禁用、打开被阻塞或事务失败 | 打开与事务各有 1.5 秒超时，失败后回退 localStorage；页面显示实际保存介质 |
| 无痕／隐私模式、嵌入 WebView、拒绝站点存储 | 可能仅在本次隐私会话保存，也可能完全不可写；不尝试识别无痕模式。保存不可用时提示，并保留当前内存使用能力 |
| Safari / iOS 或系统自动回收站点数据 | 可能提前删除不活跃站点数据；7 天是应用接受包的上限，不是浏览器保存保证 |
| 禁止本站 Cookie、清理站点数据或只复制本地包 | 缺少匹配设备 Cookie，无法恢复；需重新登录并允许必要的站点存储 |
| 多标签页 | 通过不含凭据的 storage 通知同步保存／清除结果；通知不可用时仍由服务器撤销许可阻止旧包恢复 |

浏览器每包上限 **64 KiB**，当前设备正常只保存一份记录；本地存储最多保留 8 个命名空间的记录，以限制设备标识变更或历史版本遗留的数据。正常只在一个存储介质中保存包，介质故障切换时可能暂留旧副本。每介质最多约 512 KiB 的 ASCII 包内容；按 UTF-16 计算 localStorage 配额的实现约需 1 MiB，实际磁盘占用还包含 JSON、索引及数据库开销。同一设备的新包替换旧包，不积累历史。IndexedDB 异步写入，localStorage 的同步写入最多是一份小包。浏览器本地存储通常会落盘，加密不保证端点或浏览器被控制时安全。

服务器仍只维护默认 64 个工作台、每浏览器标识最多 8 个；学校浏览器只在登录期间运行，默认并行 2 个、最长 10 分钟，完成即关闭。恢复只执行一次有超时的只读请求，不保持浏览器 profile。状态请求只含摘要和版本号，每个空闲前台标签页每 30 秒一次，登录或检查／提交期间约每 2 秒一次；后台标签页暂停状态和截图轮询，回到前台立即刷新。截图仅在登录交互期间请求。加密包仅在登录、恢复或学校 Cookie 变化后传输与保存，不附在每个请求或普通 Cookie 中。这些是默认容量与轮询约束，实际 CPU、内存和磁盘占用仍需部署后按并发量测量。

持久卷中的恢复元数据容量有显式上限，学校凭据不集中落盘；密钥是唯一需要长期保留的会话加密秘密。若系统启用 swap 或崩溃转储，运行时明文仍可能被操作系统写盘，不能据此承诺主机级“绝不落盘”。目前保持单进程、单副本，浏览器持久化没有取消原有的限速、工作台锁或防重复提交约束。

## 首次部署

准备一台可访问学校官方服务的 Linux 服务器，安装 Docker Engine 与 **Compose v2**。建议至少 2 核、2 GB 内存；默认最多并行两个登录浏览器，需按实际使用情况调整内存和并发。将专用域名的 DNS 指向服务器，开放 TCP 80/443；443/UDP 用于 HTTP/3，可按需要关闭。若设置 AAAA，IPv6 也必须可达。后端不开放 8090。

```sh
git clone https://github.com/Mr-Pian/GoodnightUESTC-Web.git
cd GoodnightUESTC-Web
cp .env.example .env
chmod 600 .env
```

编辑 `.env`，设置域名、证书通知邮箱、随机网关密钥及本次镜像标签。生成密钥：

```sh
python3 -c 'import secrets; print(secrets.token_hex(32))'
```

将输出填入 `GATEWAY_SECRET`，不要复用学校密码。`BACKEND_IMAGE` 建议使用提交号或发布版本，例如 `goodnightuestc-web:release-1`。`.env` 已在 `.gitignore` 中；Docker 构建上下文采用白名单，不包含环境配置、测试文件、离线模拟数据库或截图。无需创建成员文件或网站密码。

```sh
docker compose config --quiet
docker compose build backend
docker compose run --rm --no-deps gateway caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
docker compose up -d
docker compose ps
```

Caddy 自动申请证书并将 HTTP 跳转到 HTTPS。网关等待后端健康检查通过后启动。使用浏览器访问 `https://你的域名/`，直接登录学校账号。登录过程中弹出的画面属于本人独立的服务器浏览器；可点击图片里的字段后使用“发送输入”，或拖动验证控件。学校认证后浏览器会关闭，学校会话转入当前工作台内存。之后沿用保存实际位置、只读检查、预检查、本人最终确认和学校记录回读的单次流程。

<a id="nginx"></a>

## 使用宿主机 Nginx（可选）

已有宿主机 Nginx 管理多个站点时，可以由它负责 HTTPS 和反向代理，只运行本项目的后端容器。默认 Caddy 方案无需 Nginx；本节是替代部署方式。以下示例假设 Nginx 直接接收公网请求，域名为 `checkin.example.com`，服务部署在独立子域名的 `/` 路径。

| 端口 | Caddy 默认方案 | 宿主机 Nginx 方案 |
| --- | --- | --- |
| TCP 80 | 公网开放给 Caddy，用于 HTTPS 跳转和证书验证 | 公网开放给 Nginx，用于 HTTPS 跳转和证书验证 |
| TCP 443 | 公网开放给 Caddy | 公网开放给 Nginx |
| UDP 443 | 可选，用于 Caddy 的 HTTP/3 | 本节配置不使用，无需开放 |
| TCP 18090 | 不使用 | 只绑定宿主机 `127.0.0.1`，供 Nginx 访问后端，不开放公网 |
| 容器 TCP 8090 | 仅 Docker 网络访问 | 通过回环端口映射供宿主机 Nginx 访问，不开放公网 |

沿用首次部署中的 `.env` 初始化，将 `SITE_DOMAIN` 设置为实际域名，生成 `GATEWAY_SECRET`；保留 `.env` 的其余示例配置项，因为原 Compose 文件仍会整体解析。无需创建 Caddy 成员文件或 Nginx htpasswd 文件。

在仓库根目录创建 `compose.nginx.yaml`：

```yaml
services:
  backend:
    ports:
      - "127.0.0.1:18090:8090"
```

明确只启动后端，防止 Caddy 占用 Nginx 的 80/443：

```sh
# 仅在之前启动过本项目的 Caddy 时执行。
docker compose stop gateway

docker compose -f compose.yaml -f compose.nginx.yaml config --quiet
docker compose -f compose.yaml -f compose.nginx.yaml up -d --build backend
docker compose -f compose.yaml -f compose.nginx.yaml ps
```

后续更新与回滚也使用同样的两个 `-f` 参数，并明确指定 `backend`；不要执行未指定服务的 `up -d`，否则仍可能启动原来的 Caddy。保留 `127.0.0.1` 绑定，写成 `18090:8090` 通常会发布到所有宿主机网卡。Dockerfile 的 `EXPOSE 8090` 仅是容器端口声明，本身不会发布公网端口。

下面的 Nginx 配置放在 `http {}` 引入的站点文件中。将域名、证书路径和网关密钥替换成实际值；`X-Team-Gateway` 的值必须与后端 `.env` 中的 `GATEWAY_SECRET` 完全一致，Nginx 不会自动读取项目 `.env`。配置文件含网关密钥，应限制读取权限并且不要提交到 Git。

```nginx
# 本示例直接面向公网，按连接来源 IP 限速。
# 多名成员共用校园出口时，按实际人数调高 rate/burst。
limit_req_zone $binary_remote_addr zone=uestc_ip:10m rate=10r/s;

server {
    listen 80;
    server_name checkin.example.com;

    location /.well-known/acme-challenge/ {
        root /var/www/acme;
    }

    location / {
        return 308 https://checkin.example.com$request_uri;
    }
}

server {
    listen 443 ssl;
    server_name checkin.example.com;

    ssl_certificate     /etc/letsencrypt/live/checkin.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/checkin.example.com/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    add_header Strict-Transport-Security "max-age=31536000" always;

    client_max_body_size 96k;
    client_body_buffer_size 96k;
    client_body_timeout 15s;

    access_log off;
    error_log /var/log/nginx/uestc-error.log warn;

    location / {
        limit_req zone=uestc_ip burst=30 nodelay;
        limit_req_status 429;

        proxy_pass http://127.0.0.1:18090;
        proxy_http_version 1.1;
        proxy_set_header Connection "";
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-For $remote_addr;

        # 覆盖来源头并移除旧入口身份；不信任客户端自报的身份或 IP。
        proxy_set_header X-Client-IP $remote_addr;
        proxy_set_header X-Team-User "";
        proxy_set_header X-Team-Gateway "REPLACE_WITH_GATEWAY_SECRET";
        proxy_set_header Authorization "";

        proxy_connect_timeout 5s;
        proxy_send_timeout 15s;
        proxy_read_timeout 130s;
        proxy_cache off;
        proxy_buffering off;
        proxy_request_buffering off;
        proxy_next_upstream off;
    }
}
```

学校登录画面通过 HTTP 轮询获取，无需 WebSocket Upgrade。上述配置覆盖客户端的代理来源头、禁止代理自动重试，并关闭代理缓存和缓冲。不要另行开启请求正文、Authorization、Cookie 或 debug 日志。如果 Nginx 前面还有 CDN/代理，应只信任明确列出的代理来源后再配置 real_ip 模块，将校验后的 `$remote_addr` 传给后端；Caddy 前置代理时也须按明确的可信代理范围调整来源获取，不能直接转发客户端提供的 `X-Forwarded-For` 或 `X-Client-IP`。

安装 Nginx 及证书管理工具即可，无需安装 htpasswd 工具或分配网站密码。

完整配置要求证书文件已存在。新域名首次签发时，先只启用上面的 TCP 80 server 块并创建 `/var/www/acme`，再使用现有证书管理流程或 Certbot webroot 方式签发；证书生成后启用 443 块：

```sh
sudo mkdir -p /var/www/acme
# 先检查并加载仅含 80 server 块的站点配置。
sudo nginx -t
sudo systemctl reload nginx
sudo certbot certonly --webroot -w /var/www/acme -d checkin.example.com

# 填入证书路径和网关密钥，启用完整配置后再次检查、加载。
sudo nginx -t
sudo systemctl reload nginx
```

为证书安排自动续期，并在成功续期后 reload Nginx。加载后验证：无需网站账号，访问 `/` 和 `/api/status` 应返回 200，尚未登录学校时 `account` 为 `null`；不带可信网关头直接访问 `http://127.0.0.1:18090/api/status` 返回 403。公网防火墙只需放行该站点的 TCP 80/443。

如果 Nginx 也运行在 Docker 中，应让它加入后端所在的 Docker 网络，代理目标改为 `http://backend:8090`，后端不设置宿主机 `ports`；容器内的 `127.0.0.1` 指向该容器自身。Nginx 方式下无需为本项目的 Caddy 持久卷申请证书；后端 `attempts` 卷仍须保留。

## 配置项

| 变量 | 默认值 | 用途 |
| --- | --- | --- |
| `SITE_DOMAIN` | 必填 | 公网域名，不带协议、路径、结尾斜线；建议使用独立子域名 |
| `ACME_EMAIL` | 必填 | HTTPS 证书通知邮箱 |
| `GATEWAY_SECRET` | 必填 | 至少 32 个 ASCII 字符；建议生成 64 位十六进制字符串；仅供网关到后端认证 |
| `BACKEND_IMAGE` | `goodnightuestc-web:local` | 本地构建镜像标签，应按版本区分以便回滚 |
| `SESSION_IDLE_SECONDS` | `1800` | 距离最后一次请求的会话空闲期限；页面的状态轮询也算活动 |
| `SESSION_LIFETIME_SECONDS` | `28800` | 内存工作台最长 8 小时，清理后可从浏览器恢复 |
| `SESSION_PACKAGE_TTL_SECONDS` | `604800` | 浏览器加密包的绝对期限，默认 7 天；修改只影响新签发的包 |
| `MAX_SAVED_DEVICES` | `1024` | 服务端最多保留的有效恢复许可数 |
| `MAX_SESSIONS` | `64` | 单个后端最多保留的工作台数量 |
| `MAX_SESSIONS_PER_BROWSER` | `8` | 每个随机浏览器标识的工作台数量上限，普通标签页通常共用一个 |
| `MAX_CONCURRENT_LOGINS` | `2` | 全局并行 Chromium 登录数量，超出返回 429 |
| `SOURCE_RATE_MULTIPLIER` | `4` | 来源 IP 预算为浏览器预算的倍数；校园出口多人共用 IP 时可按需调高，必须为正整数 |

`PUBLIC_ORIGIN` 由 Compose 从域名生成，服务启动时要求 HTTPS。`DATA_DIR` 在直接运行 `server.py` 时可设定，容器默认 `/data`。Docker 默认 `UESTC_NETWORK_MODE=direct`；如果服务器须通过测试组的出口代理访问学校，可在后端 environment 中显式添加 `HTTPS_PROXY` 并将模式改为 `system`。容器内的 `127.0.0.1` 指向容器自身。学校的 TLS 证书校验始终启用。

请求同时消耗浏览器预算与可信代理提供的来源 IP 预算，超限返回 429 和 `Retry-After`。限速适用于登录前后，不按学校账号计数。默认如下：

| 请求类别 | 每浏览器标识 | 每来源 IP（默认 4 倍） |
| --- | --- | --- |
| 全部应用请求 | 240 次／分钟 | 960 次／分钟 |
| 普通写操作 | 30 次／分钟 | 120 次／分钟 |
| 登录输入 | 180 次／分钟 | 720 次／分钟 |
| 启动学校登录 | 3 次／5 分钟 | 12 次／5 分钟 |
| 恢复会话包 | 5 次／5 分钟 | 20 次／5 分钟 |

清 Cookie 或换浏览器不会重置同一来源 IP 的预算；换 IP 不会重置同一设备 Cookie 的预算。多人共用校园出口会共用 IP 预算，可用 `SOURCE_RATE_MULTIPLIER` 调整。IP 限速与浏览器标识不作为人员白名单，也不能阻止拥有多个来源 IP 的客户端消耗资源；全局工作台和 Chromium 并发上限仍生效。Nginx 示例还包含按连接 IP 的前置限速。

普通应用请求体上限 16 KiB；只有 `/api/session/restore` 放宽到 96 KiB 以承载最多 64 KiB 的包与 JSON。Nginx 和 Waitress 外层上限同步为 96 KiB。学校单次网络请求默认 8 秒超时；登录最多等待 10 分钟，取消会在当前浏览器／网络调用返回后生效。Caddy 等待后端响应头最多 130 秒。前端等待签到最多 120 秒；即使前端超时，也只能先只读核对结果，不能据此认为未提交或自动重发。

服务端内存注册表和限速器限定为**单进程、单副本**；Waitress 用 8 个线程处理并发。不要加多进程 WSGI worker，也不要 `--scale backend=...`。多实例部署需要重新设计共享会话、限速及提交互斥机制。

## 验证与日志

```sh
docker compose exec -T backend python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8090/healthz', timeout=2).status)"
docker compose logs --tail=100 backend gateway
```

`/healthz` 只报告进程可响应，不访问学校，也不创建会话；它不能证明学校网络或登录可用。外网无需网站密码即可打开页面，首次 `/api/status` 返回 `account: null`；有效学校登录前不能执行学校查询或提交。进入页面后可先运行“离线自检”。实际学校登录和提交由本人按授权测试范围操作。

应用不记录密码、Cookie、token、请求正文、浏览器控制台或异常原文。Caddy 默认不启用访问日志，错误日志过滤请求头和 URI；容器日志各保留最多 3 个 10 MB 文件。排查问题不要开启请求体、HTTP trace、Playwright trace、HAR、浏览器录制或打印环境变量；这些可能包含凭据。账号资料与登录画面也不要放进问题反馈截图。

## 更新、回滚和会话维护

**从带入口认证的旧版升级：** 同步更新后端、`compose.yaml`、`.env` 和代理配置。Caddy 使用新版 Caddyfile 并重新创建网关；Nginx 删除原 `auth_basic` / `auth_basic_user_file`，按上文设置 `X-Client-IP`、内部密钥并清除旧身份头，然后检查并 reload。将旧 `MAX_SESSIONS_PER_USER` 替换为 `MAX_SESSIONS_PER_BROWSER`，移除已无用途的 `MAX_SAVED_DEVICES_PER_USER`，按需增加 `SOURCE_RATE_MULTIPLIER`。旧成员文件不再挂载或读取，可自行移出部署目录；`.gitignore` 继续忽略旧文件以防意外提交。

旧 `GS1` 会话包与入口账号绑定，本版改为仅绑定站点和浏览器的 `GS2` 包，**升级后需重新完成一次学校登录**。不接收客户端自报的旧入口身份进行迁移。原密钥、数据库和防重复日志全部保留；旧许可行在到期后的下次签发时清理，清理前仍计入许可总容量。回滚到旧版时，新版包也无法恢复，需要配套恢复旧代理配置及账号认证并重新登录。

更新前等待正在执行的单次操作完成；重启清除所有内存工作台，但保留了浏览器包、设备 Cookie、密钥和恢复许可的成员可自动恢复学校登录。后端收到停止信号会停止接受新连接，并为已执行的请求最多等待 125 秒；Compose 停止宽限期为 140 秒。记录当前 Git 提交与 `.env` 中的镜像标签，保留旧镜像及整个 `attempts` 卷（含密钥和恢复许可）。

```sh
git pull --ff-only origin main
# 编辑 .env，把 BACKEND_IMAGE 改成本次新的唯一标签。
docker compose config --quiet
docker compose build backend
docker compose up -d --no-deps backend
docker compose ps
```

若这次同时更新了 `deploy/Caddyfile`，重新创建网关加载配置：

```sh
docker compose up -d --force-recreate gateway
```

回滚应用时，将 `.env` 的 `BACKEND_IMAGE` 改回上一个保留的标签：

```sh
docker compose up -d --no-build --no-deps backend
docker compose ps
```

如果网关配置也有变化，还需恢复对应版本的代理配置。当前版本保留原有防重复日志与许可表结构；不要删除包密钥或许可表。今后若涉及数据库迁移，应先停服备份卷，再按对应版本说明回滚。

本站不维护成员账号名单。使用“清除学校会话与本机保存”撤销当前设备的学校会话恢复许可；共用电脑请关闭记住登录，并在使用结束后清除会话。重启后端只清理内存，不撤销仍有效的浏览器包。

```sh
docker compose stop
docker compose start
```

`stop/start` 保留持久卷，内存学校会话会消失，符合恢复条件的浏览器可自动恢复。不要用 `docker compose down -v` 做日常更新，它会删除防重复日志、会话加密密钥、恢复许可和证书。备份时先停止后端，再完整备份 `goodnightuestc_attempts` 卷；证书卷也应按测试组运维规范保护。主机启用 swap 或崩溃转储时，内存内容仍可能被操作系统写入磁盘，内存模式不构成主机级“绝不落盘”保证。

## 测试范围

运行 `python -m unittest discover -s tests -v` 可执行合成测试，覆盖多用户隔离、确认令牌、网关与 CSRF、到期清理、限速和单次提交防重复行为。`tests/hosted_ui_smoke.py` 使用模拟学校页面检查多用户远程登录、地图选点、模拟确认提交、离线自检和手机布局，不访问真实学校。界面测试默认使用 Playwright Chromium，安装与运行命令见主 [README](../README.md#开发与测试)。

本机装有 Caddy 时，可运行 `python tests/gateway_smoke.py`（或用 `CADDY_BIN` 指定可执行文件），通过回环 HTTP 连接验证无需网站密码访问页面与状态接口、伪造来源头和内部密钥被覆盖、旧身份与 Authorization 头被删除、后端直连返回 403。它使用临时配置和数据，不申请公网证书。唯一 CI `.github/workflows/docker.yml` 负责该代理测试、合成单元测试、Linux 镜像构建、非 root 只读容器启动、健康检查、容器内 Chromium 截图和两个托管界面测试；测试文件仅以只读挂载加入，所有步骤均不请求学校服务。

离线模拟器保留在源码中，Docker 镜像不包含或启动它。此部署没有新增定时提交任务。合成测试和模拟页面通过不代表真实学校 API 提交成功；容器实测、证书签发和学校本人登录需分别核验。`tests/session_ui_smoke.py` 使用合成快照验证浏览器保存、工作台清理／后端重启后的恢复、存储降级与清除；不请求真实学校服务。Firefox / Safari 尚需在目标设备验收，不能将单一浏览器模拟故障的结果表述为其他浏览器的实测。
