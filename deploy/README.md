# Docker 与自托管部署

本 fork 基于 `LCX-luo/GoodnightUESTC`，只维护 Docker / Web 部署，正式入口为 `server.py`。学校登录使用独立 Chromium，不需要服务器桌面或 VNC；成员只需访问网页。

## 架构与保存范围

浏览器请求经过部署者管理的 HTTPS 反向代理后进入后端，页面不要求额外的网站账号或 HTTP Basic Auth。用户直接完成学校统一认证；学校相关操作要求有效学校会话，本站不另设测试组白名单，任何具备相应学校账号与业务权限的人均可使用。后端校验内部代理密钥、公开 Host、Origin 和 CSRF，并按浏览器及来源 IP 限速。用户只需浏览器，无需在本机运行 Python。`hosted.py` 管理隔离的工作台与资源，`session_packages.py` 负责加密会话包、设备绑定与恢复许可。

`GATEWAY_SECRET` 是反向代理与后端之间的内部共享密钥，由运维配置，用户无需输入；它不承担成员登录功能。代理覆盖 `X-Team-Gateway`、`X-Client-IP` 并删除旧 `X-Team-User` 和 Authorization 头。后端仅在密钥校验通过后信任代理提供的来源 IP，不使用客户端自报的身份或 `X-Forwarded-For`。代理前若有 CDN 或其他入口，需由部署者限定可信来源后确定客户端 IP。

1. 学号、密码、验证码通过 HTTPS 发到后端，在独立 Chromium context 中完成学校登录；密码仅供本次登录，不保存用于自动重登。
2. 登录成功后关闭 Chromium，取得学校 Cookie、学号、User-Agent 和保存时间的快照。默认开启“在此浏览器记住学校登录”，可随时关闭；共用设备请关闭。
3. 后端以 AES-256-GCM 加密快照，绑定当前公开站点及随机的 HttpOnly 浏览器设备 Cookie。网页只收到加密包，不会收到明文学校 Cookie；签名不等于加密，本实现不把快照塞进 Flask 签名 Cookie。
4. 加密包由用户浏览器保存。后端工作台被清理或容器更新后，网页上传包，后端验证完整性、期限、设备绑定与恢复许可，再以有超时的学校只读请求校验。若仅签到应用授权失效，通过只读 GET 重新进入应用并再次校验；学校仍认可会话时恢复。该过程不启动 Chromium，不提交账号密码或签到。
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

Chromium 临时 profile、缓存和临时缓冲文件位于 `/tmp` tmpfs。后端使用非 root、只读根目录、默认 Docker seccomp、去除 capabilities 和禁止提权。Playwright 默认关闭 Chromium 自身 sandbox，隔离依赖容器限制与学校域名白名单；不要挂载 Docker socket。

<a id="sessions"></a>

## 登录状态与有效期

**默认开启记住登录后，今天登录、明天用同一浏览器打开，可以自动尝试恢复，不再因后端内存清理就要求重登。** 学校会话是否仍有效由学校决定，不能保证一定可跨日。未开启保存或浏览器拒绝保存时，只有当前内存工作台有效。

| 期限／条件 | 当前默认行为 |
| --- | --- |
| 浏览器加密包 | `SESSION_PACKAGE_TTL_SECONDS=604800`，从首次签发最多 7 天；恢复、读取和 Cookie 更新均不延期，重新完成学校登录并保存才签发新期限 |
| 工作台空闲 | `SESSION_IDLE_SECONDS=1800`，30 分钟没有请求后清理服务器内存；恢复时新建工作台，不要求重新输入学校密码 |
| 工作台绝对期限 | `SESSION_LIFETIME_SECONDS=28800`，从工作台创建最多 8 小时；首次访问即可能创建。到期可恢复加密包，这不是学校 Cookie 的有效期 |
| 学校会话 | 学校 Cookie 到期和学校服务端策略可能提前失效；只读恢复后仍被学校明确判定认证失效时，撤销当前设备恢复许可并清除本机保存，要求重登；签到业务拒绝不等同认证失效 |
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

### 浏览器重开后的恢复排查

正常模式下关闭再打开同一浏览器，不应仅因此丢失已保存的登录。页面先建立新的内存工作台，再读取本机包并尝试恢复；“学校记住设备／免短信验证”与本站加密包是两套机制，免短信不能证明本站恢复成功。

| “记住学校登录”下方提示 | 核查方向 |
| --- | --- |
| 此浏览器未找到可恢复的学校登录 | 当前设备标识对应的本机包未读到。确认与保存时是同一个浏览器 profile、普通模式及完整 HTTPS origin（协议、域名、端口）；检查关闭浏览器时自动清除 Cookie／站点数据的设置与清理扩展。设备 Cookie 被删除或服务器加密密钥变化也会改变保存命名空间，即使旧包仍留在存储中也不能直接恢复 |
| 本地存储不可用／无法保存登录 | 检查本站存储权限、磁盘空间与隐私设置；只有显示“已加密保存在此浏览器”才代表写入成功 |
| 学校请求超时／无法连接学校网络 | 包保留，先检查服务器到学校的网络，再点“重试保存或恢复”；无需清空站点数据 |
| 保存的登录已到期／被清除或撤销／无法恢复 | 检查包期限、设备 Cookie、服务器密钥及许可数据库是否保留；包绑定不匹配时不能通过复制包绕过 |
| 学校会话已失效／应用未授权 | 只读重新进入应用后仍无法校验，需本人重新完成学校认证 |

复现时先保留原状态，在本站开发者工具 Console 执行下列摘要检查。它只输出可用性、是否有包、期限和提示，不打印账号、设备标识、Cookie 或包内容：

```js
(async () => {
  const state = await api('/api/status');
  const saved = await SchoolSessionStorage.get(state.persistence.owner);
  console.log(JSON.stringify({
    loggedIn: Boolean(state.account),
    storageAvailable: saved.available,
    hasSavedPackage: Boolean(saved.record && saved.record.token),
    savedPackageExpiresAt: saved.record && saved.record.expires_at
      ? new Date(saved.record.expires_at * 1000).toISOString() : null,
    message: document.getElementById('remember-session-message').textContent
  }, null, 2));
})();
```

若 Network 中 `/api/session/restore` 失败，只记录 HTTP 状态及 JSON 中的 `reason`、`stage`、`error`；不要分享请求体、请求头、完整 HAR 或浏览器存储导出。没有发起恢复请求且摘要显示无包时，应先排查本地保存和设备绑定。不要删除数据卷或清空站点数据试错。

### 提交结果与手动重试

每次提交都要求新的短期确认令牌和本人实际位置确认，并重新读取当天记录、围栏与时段。单次请求最多发送一次签到 POST，明确拒绝也不会自动重发。

| 本地尝试结果 | 后续行为 |
| --- | --- |
| `rejected`：学校明确业务拒绝，回读既未确认成功，也未发现其他终态 | 可重新预检查并再次确认，原子争抢成功后手动提交；若尝试结果未能保存则继续防重，先由部署者检查存储 |
| `started`：正在执行，或发送期间进程异常退出 | 保留防重，仅核对学校记录 |
| `unknown`：超时、断线、异常响应或学校返回接受但回读未确认 | 保留防重，仅核对学校记录；`PENDING` 本身不能排除请求仍在处理中 |
| `confirmed` / `record_changed` 或学校已有签到、报告、豁免记录 | 不再次提交；以学校当天记录为准 |

升级兼容已有 `attempts.sqlite3` 的 `rejected` 行，无需数据迁移，也无需删除当天记录。其他状态继续保持防重。页面显示学校业务码、HTTP 状态和脱敏后的错误文字，供定位首次拒绝原因；这些文字不写入持久尝试日志。

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

服务器仍只维护默认 64 个工作台、每浏览器标识最多 8 个；学校浏览器只在登录期间运行，默认并行 2 个、最长 10 分钟，完成即关闭。恢复以有超时的只读请求校验，必要时只读重新进入签到应用，不保持浏览器 profile。状态请求只含摘要和版本号，每个空闲前台标签页每 30 秒一次，登录或检查／提交期间约每 2 秒一次；后台标签页暂停状态和截图轮询，回到前台立即刷新。截图仅在登录交互期间请求。加密包仅在登录、恢复或学校 Cookie 变化后传输与保存，不附在每个请求或普通 Cookie 中。这些是默认容量与轮询约束，实际 CPU、内存和磁盘占用仍需部署后按并发量测量。

持久卷中的恢复元数据容量有显式上限，学校凭据不集中落盘；密钥是唯一需要长期保留的会话加密秘密。若系统启用 swap 或崩溃转储，运行时明文仍可能被操作系统写盘，不能据此承诺主机级“绝不落盘”。目前保持单进程、单副本，浏览器持久化没有取消原有的限速、工作台锁或防重复提交约束。

## 后端安装与启动

仓库只交付应用容器：Web 静态资源、Waitress 和学校登录所需 Chromium。域名、公网端口、反向代理及 HTTPS 证书由部署者管理；Compose 不启动代理，不申请证书，不修改宿主机配置。推荐 Linux 服务器 2 核、4 GiB 内存，2 GiB 小主机需减并发并实测，详见[资源预算与观测](RESOURCES.md)。逐条命令见 **[固定版本部署流程](QUICKSTART.md)**。

要求 Docker CLI / Engine 均 >= 24.0.0、Compose >= 2.20.0；可选共享网络覆盖文件需要 **Compose >= 2.24.4**。源码构建额外要求 **buildx >= 0.12.0** 与可访问的 builder，不支持 legacy builder 或 `DOCKER_BUILDKIT=0`。预构建镜像运行不要求 buildx。安装器另需宿主机 Python 3.8+ 标准库，不需要 pip 安装应用依赖。

```sh
# 不拉取镜像或下载应用依赖，先检查本地工具和 daemon 连接。
python3 deploy/preflight.py
# 仅使用已发布镜像时：
python3 deploy/preflight.py --image-only
```

安装器也会在询问、保存配置和下载依赖前完成该检查，错误会区分版本、daemon 权限与 buildx / builder。Docker context、buildx 存在不表示 PyPI、Debian 源或 Chromium 下载网络已通。

### 推荐：交互安装

使用 Release 的 Linux amd64 预构建镜像，跳过服务器本地构建：

```sh
python3 deploy/install.py --image ghcr.io/mr-pian/goodnightuestc-web:1.1.0
```

对应镜像必须已成功发布并允许拉取；该模式将镜像引用写入 `.env`，执行 `pull` 与 `up --no-build --wait`。也可组合 `--configure-only` 或 `--proxy-network NAME`。GHCR 首次发布默认访问级别需由维护者确认，未公开时可改用下方源码构建。

自行构建源码：

```sh
git clone --branch web https://github.com/Mr-Pian/GoodnightUESTC-Web.git
cd GoodnightUESTC-Web
python3 deploy/install.py
```

安装器在服务器终端依次询问 `PUBLIC_ORIGIN` 和运维自己申请的**天地图地图服务 Key**。Key 输入不回显，首次安装不能为空，详见[地图配置与排障](#maps)。安装器设置 `MAP_PROVIDER=tianditu`，生成缺失的 `GATEWAY_SECRET`，以原子替换方式保存权限 `0600` 的 `.env`，再运行 Compose 配置校验、`up -d --build --wait backend` 和状态查看。内部代理密钥与地图 Key 不打印到终端，也不作为命令行参数或构建参数传递。运维从本机 `.env` 将 `GATEWAY_SECRET` 配置到自己的反向代理。

再次运行时，地址和 Key 可直接回车保留；不会轮换已有内部密钥，不删除数据卷，并保留自定义端口、镜像标签、会话限制及项目名等其他配置。新安装器会将默认底图调整为天地图；切换 Key 时再次输入新值即可。输入未完成或取消时不写文件；Docker 后续失败时保留已保存的配置，修复后重试。已有部署更新前仍需等待正在执行的学校操作完成，按[更新、回滚和会话维护](#更新回滚和会话维护)保留原项目名和卷。

只准备配置、暂不构建启动：

```sh
python3 deploy/install.py --configure-only
```

已有容器代理网络时，安装器可使用共享网络覆盖文件：

```sh
python3 deploy/install.py --proxy-network my-proxy-network
```

网络需由运维事先建立并接入代理，安装器会将名称写入 `.env`，不创建网络。此命令要求 Compose >= 2.24.4，并移除宿主机端口映射，详见下文。再次使用安装器管理同一共享网络部署时继续带 `--proxy-network`；手动 Compose 管理继续带相同的两个 `-f` 参数。

安装器固定使用仓库根目录的 `.env`，启动子进程时不接受 shell 中同名 `PUBLIC_ORIGIN`、`GATEWAY_SECRET`、`MAP_PROVIDER`、`TIANDITU_KEY`、`PROXY_NETWORK` 覆盖刚保存的值，构建出口代理变量照常保留。它不加载或执行 `.env` 的 shell 代码，受管理的配置项支持单行字面值；复杂插值、转义或符号链接配置请使用下文手动方式。不要通过管道把 Key 传给安装器；无交互 CI / 自动化使用 `.env` 或平台的运行时配置直接启动 Compose。直接运行 `docker compose` 本身不会弹出输入提示。

### 手动配置与自动化

拉取代码后，准备自己的环境文件，或由部署平台注入相同配置：

```sh
cp .env.example .env
chmod 600 .env
python3 -c 'import secrets; print(secrets.token_hex(32))'
```

编辑 `.env`：

- `PUBLIC_ORIGIN`：浏览器实际访问的完整 HTTPS origin，例如 `https://checkin.example.com:60000`。使用小写域名，不含路径、尾部斜线、查询或凭据；当前应用部署在站点根路径，不支持子路径挂载。
- `GATEWAY_SECRET`：填入生成的随机值，与现有代理配置中的内部密钥一致。不要复用学校密码；无需创建成员文件或网站账号。
- `BACKEND_PORT`：宿主机代理连接的本地端口，默认 `18090`，仅绑定 `127.0.0.1`。
- `BACKEND_IMAGE`：本地构建使用唯一标签，例如 `goodnightuestc-web:你的提交号`；发行镜像使用 `ghcr.io/mr-pian/goodnightuestc-web:1.1.0` 或其 digest，保留旧镜像供回滚。不要以 `latest` 作为生产回滚依据。
- `MAP_PROVIDER` / `TIANDITU_KEY`：模板默认 `tianditu`，必须填自己的天地图地图服务 Key，配置方法见[地图配置与排障](#maps)。需要 OSM 的手动部署可显式改为 `osm`；未设置 provider 的旧配置仍兼容 `auto`。

```sh
docker compose config --quiet
docker compose build
docker compose up -d --wait
docker compose ps
curl --fail http://127.0.0.1:18090/healthz
```

默认只启动 `backend`，不绑定公网 80/443，不要求证书配置。容器内监听 TCP 8090；Dockerfile 的 `EXPOSE` 仅声明端口，本身不会发布端口。`/healthz` 返回 200 仅代表应用可响应；此时直接访问回环端口的页面或 `/api/status` 返回 **403 是预期行为**，需按下节接入可信代理后使用。浏览器学校登录仍通过该代理的 HTTPS 入口进行。

### 现有容器代理：使用共享网络

如果反向代理也在 Docker 中运行，由部署者创建或选择受控的共享网络，将自己的代理接入，再使用仓库提供的覆盖文件。不要让不受信任的容器加入该网络。

```sh
# 换成自己的网络名称；不存在时由部署者创建。
export PROXY_NETWORK=my-proxy-network
docker compose -f compose.yaml -f deploy/compose.proxy-network.yaml config --quiet
docker compose -f compose.yaml -f deploy/compose.proxy-network.yaml up -d --build --wait
```

此方式需要 Compose >= 2.24.4；覆盖文件用 `!reset []` 移除宿主机端口映射，并将 `app` 网络指向已有网络。代理目标为 `http://backend:8090`，容器中的 `127.0.0.1` 指向容器自身。同一共享网络运行多个同名服务时，部署者应为后端安排唯一网络别名并相应调整代理目标。后续管理、更新、备份等命令均使用同样的两个 `-f` 参数及同一 `PROXY_NETWORK`。

<a id="proxy-contract"></a>

## 反向代理接入约定

可使用已有 Nginx、Caddy、Traefik 或其他代理。本项目只规定应用需要的转发行为，不安装代理或管理证书。公网 HTTPS 的证书、可信链和可达端口由部署者验证。

| 项目 | 要求 |
| --- | --- |
| 站点与 `Host` | 代理只接受配置的站点，将完整外部 authority 传给后端；必须匹配 `PUBLIC_ORIGIN`，包括非标准 HTTPS 端口。例如 `checkin.example.com:60000`。Nginx 的 `$host` 通常不包含端口；可在限定站点中设置固定 authority，或先验证再使用包含端口的 Host，不能无条件信任任意客户端 Host |
| `X-Team-Gateway` | 代理覆盖为私有 `GATEWAY_SECRET`，不接受客户端自报值；代理不会自动读取本项目 `.env`，需部署者配对配置 |
| `X-Client-IP` | 覆盖为可信客户端 IP。前置 CDN 或其他代理时，先限定可信来源，再解析实际 IP；不直接透传用户自报头 |
| `Authorization` / `X-Team-User` | 清除客户端提供的值，不将其用作本站成员认证。学校认证在应用内部完成 |
| 浏览器请求 | 保留 Cookie、`Origin`、`X-CSRF-Token`、响应 `Set-Cookie` 和安全响应头。维持 Host / Origin / CSRF、Secure Cookie 和 CSP 校验，不添加 `unsafe-eval` |
| 超时 | 建议连接 5 秒、发送 15 秒、读取至少 130 秒；前端签到等待最多 120 秒。超时或断开不代表学校未收到请求，不能自动重发 |
| 重试 | 禁止代理对写请求自动重试或故障转移重发；应用的真实签到 POST 也只发送一次 |
| 缓存、缓冲 | 不缓存私有页面和 API；禁用响应、请求缓冲可避免敏感正文落到代理临时文件。请求体允许 96 KiB，不能意外截断会话包恢复请求 |
| 日志 | 不记录密码、Cookie、token、内部密钥、请求正文和登录画面；代理调试日志也应按此处理 |
| 连接协议 | 远程登录使用 HTTP 轮询，不要求 WebSocket。HTTPS 在代理终止，可信内部网络上用 HTTP 访问应用 |
| 限速 | 后端已有浏览器及来源 IP 限速；代理可另设适合实际人数的限制，校园共用出口需考虑 NAT |

接入后，未经学校登录的 `/`、`/api/status` 应返回 200，状态中 `account` 为 `null`；无内部代理凭据直连 `/api/status` 应返回 403。学校查询和提交仍要求有效学校会话。不要把“容器健康”当作代理、证书和学校流程均已通过。

## 配置项

| 变量 | 默认值 | 用途 |
| --- | --- | --- |
| `PUBLIC_ORIGIN` | 必填 | 完整外部 HTTPS origin，允许非标准端口，格式要求见上文 |
| `GATEWAY_SECRET` | 必填 | 至少 32 个 ASCII 字符，推荐 64 位十六进制随机值；仅供代理到后端校验 |
| `BACKEND_PORT` | `18090` | 宿主机回环端口，映射到容器 8090；共享网络覆盖文件不发布此端口 |
| `BACKEND_IMAGE` | `goodnightuestc-web:local` | 本地构建的镜像标签，按版本保留以便回滚 |
| `PYTHON_IMAGE` | `python:3.12-slim-bookworm` | 可选的构建期基础镜像引用；使用可信、兼容的 Python 3.12 / Debian Bookworm 镜像，并确认目标平台；不进入运行时环境 |
| `MAP_PROVIDER` | 模板 / 安装器为 `tianditu`；未设置时 `auto` | 默认底图：`tianditu` 强制要求 Key；`auto` 有天地图 Key 时选天地图，否则选 OSM；`osm` 默认 OSM |
| `TIANDITU_KEY` | 空 | 部署者申请的天地图地图服务 Key，非空时页面可选天地图；此值会发送给浏览器，不能当作服务器私密凭据 |
| `SESSION_IDLE_SECONDS` | `1800` | 距离最后一次请求的工作台空闲期限；页面状态轮询也算活动 |
| `SESSION_LIFETIME_SECONDS` | `28800` | 内存工作台最长 8 小时，清理后可从浏览器恢复 |
| `SESSION_PACKAGE_TTL_SECONDS` | `604800` | 浏览器加密包绝对期限，默认 7 天；修改只影响新签发的包 |
| `MAX_SAVED_DEVICES` | `1024` | 服务端最多保留的有效恢复许可数 |
| `MAX_SESSIONS` | `64` | 单个后端最多保留的工作台数量 |
| `MAX_SESSIONS_PER_BROWSER` | `8` | 每个浏览器标识的工作台上限，普通标签页通常共享一个 |
| `MAX_CONCURRENT_LOGINS` | `2` | 全局并行 Chromium 登录数，超出返回 429 |
| `SOURCE_RATE_MULTIPLIER` | `4` | 来源 IP 预算为浏览器预算的倍数，校园 NAT 可按需调高，必须为正整数 |
| `PROXY_NETWORK` | 仅共享网络方式必填 | 已有 Docker 网络名称，仅用于 Compose 覆盖文件 |

容器默认 `DATA_DIR=/data`。Compose 保留非 root UID/GID 10001、只读根目录、`/tmp` tmpfs、256 MiB 共享内存、移除 capabilities、禁止提权，以及 2 GiB 内存、2 核 CPU 和 256 PID 上限。按实际登录并发测量资源需求，不要直接增加 WSGI worker 或副本：当前内存注册表、限速和互斥要求**单进程、单副本**，Waitress 使用 8 个线程。

后端限速同时消耗浏览器和来源 IP 预算：每浏览器全部请求 240 次/分钟、普通写请求 30 次/分钟、远程输入 180 次/分钟、学校登录 3 次/5 分钟、会话恢复 5 次/5 分钟。来源 IP 预算乘以 `SOURCE_RATE_MULTIPLIER`；超限返回 429 和 `Retry-After`。

<a id="maps"></a>

## 地图配置与排障

前端使用随应用提供的 Leaflet，不加载远程地图脚本 SDK。天地图通过官方 HTTPS WMTS 加载矢量底图（`vec_w`）与中文注记（`cva_w`）两层；OpenStreetMap 保留为可选底图。天地图需要部署者在[天地图开发资源](https://lbs.tianditu.gov.cn/)申请自己的、允许网页调用地图服务的 Key，服务接口依据[官方地图服务文档](https://lbs.tianditu.gov.cn/server/MapService.html)。

推荐安装方式 `python3 deploy/install.py` 会在服务器终端请求填写 Key，不从镜像作者处共享 Key。手动部署时在服务器 `.env` 配置：

```dotenv
MAP_PROVIDER=tianditu
TIANDITU_KEY=填写自己的天地图Key
```

更新到包含地图功能的代码后执行 `docker compose up -d --build backend`，然后刷新网页；共享网络部署仍使用原有的两个 `-f` 参数。Key 在运行时读取，不写入镜像，配置变更需要重新创建容器，单纯 `docker compose restart` 不会载入新的环境变量。

`MAP_PROVIDER=auto` 仅根据是否配置 Key 决定初始底图，**不会在网络失败时自动回退或反复重试**。国内部署建议显式填 `tianditu`，遗漏 Key 会在启动时给出配置错误。填写 Key 后，页面提供真正的“地图底图”切换和失败后的“重试底图”；切换底图不修改标记、输入坐标或已保存位置。“输入坐标系”中的 GCJ-02（高德）只是坐标来源，并非高德底图。

Key 会出现在 `/api/map-config` 响应和地图图片 URL 中，浏览器可见。使用专用地图 Key，按天地图平台规则限制允许的站点、服务权限与调用配额；不要复用学校密码、Cookie、token 或内部 `GATEWAY_SECRET`。真实 `.env` 不提交到 Git。页面保留“引自 天地图”的来源链接。

### Key、部署地址与白名单的关系

`.env` 的 Key 必须对应运维在天地图控制台申请的地图服务应用。`PUBLIC_ORIGIN` 则是用户实际打开的 HTTPS 地址；它用于本站 Host / Origin 校验，**不会自动同步天地图白名单**。天地图按该应用的服务权限、配额与来源规则独立决定是否返回图片。因此页面和后端都正常、只有地图空白时，应单独检查控制台的来源限制。

本站与图片元素采用 `strict-origin-when-cross-origin`，浏览器向天地图发送的 Referer 通常是站点 origin，例如 `https://checkin.example.com:60000/`，不带业务路径；非标准端口、子域名、HTTPS 与测试临时域名可能使它与控制台登记值不同。**控制台具体接受域名、完整 URL、端口还是通配规则，以天地图当前填写说明为准**，不要机械复制一个带路径的整页地址，也不要假定加入服务器 IP 就允许所有用户浏览器。

排查顺序：

1. 在失败浏览器确认地址栏最终 HTTPS 地址，核对本站 `PUBLIC_ORIGIN`；重定向后的主域 / 子域也需考虑。
2. 在天地图控制台确认 Key 所属应用、地图服务权限与配额，按平台规则允许该浏览器站点来源。更换域名或端口后重新核对，保存规则后留出平台生效时间。
3. 在应用页 Network 中检查 `vec_w/wmts` 与 `cva_w/wmts` 请求的 Referer、状态和响应类型。200 也可能返回错误文本，不能只看 HTTP 状态。Key 截图前遮盖 `tk`；不把完整 URL 发到公开 issue。
4. 若实际 Referer 缺失，检查代理是否覆盖 Referrer-Policy、浏览器扩展或隐私设置；不通过关闭 HTTPS 校验、全站放开 CSP 或取消所有 Key 限制解决。
5. 更新 Key 后执行 `docker compose up -d --no-build --force-recreate backend` 并刷新页面；共享网络使用原 `-f` 组合。单纯 `restart` 不加载新环境。

用户部署反馈中曾出现 Key 白名单不匹配导致空白，修正后地图可见。这与 Docker 构建代理无关。直接打开瓦片 URL、服务器 curl 成功或某一台设备成功，均不能替代实际页面来源下的验收。

地图图片由**用户浏览器直连地图服务商**，服务商会看到设备 IP、站点来源和浏览区域；这些跨域图片请求不携带本站或学校 Cookie。Docker 不增加地图代理、监听端口或数据卷，服务器无需为地图开放新的入站端口。服务器的学校访问代理和 `UESTC_NETWORK_MODE` 不影响用户浏览器加载地图。天地图每个视图请求两层图片，浏览器正常使用 HTTP 缓存；页面只保留附近少量瓦片，不批量下载或预取离线地图。

后端已按配置发送 CSP：地图仅增加图片来源，`script-src` 和 `connect-src` 仍为 `'self'`。优先保留后端响应的 CSP。若现有 Nginx 或其他代理另外设置 CSP，多个策略会同时生效，更宽松的一份不能覆盖更严格的一份；其 `img-src` 也必须允许 `'self'`、`data:` 和启用的地图图片域名：

```text
https://tile.openstreetmap.org
https://t0.tianditu.gov.cn https://t1.tianditu.gov.cn https://t2.tianditu.gov.cn https://t3.tianditu.gov.cn
https://t4.tianditu.gov.cn https://t5.tianditu.gov.cn https://t6.tianditu.gov.cn https://t7.tianditu.gov.cn
```

不需要为此允许远程脚本或跨域 API fetch，也不需要 `unsafe-eval`、通配符域名或关闭 CSP。

底图失败时，在**失败设备**的开发者工具 Network 中检查图片请求及响应：

- `tile.openstreetmap.org` 的 `ERR_CONNECTION_CLOSED` 是该设备到 OSM 的连接失败；服务器能打开不代表设备能打开。检查是否已配置天地图并重建容器，或在页面切换到底图“天地图”。
- 请求已发往 `t0`–`t7.tianditu.gov.cn`，但出现权限、配额或其他服务错误时，核对自己的 Key 是否允许地图服务及当前站点，查看平台配额和响应内容。服务返回的错误内容也可能无法作为图片显示。
- 控制台提示 CSP 拦截时，核对响应中是否叠加了代理的 CSP；不要通过放宽脚本策略解决图片来源限制。HTTP 状态和连接错误属于其他排查方向。
- 直接在同一设备打开 Network 中的失败图片 URL 可辅助判断连接情况，但站点来源不同可能影响 Key 校验，最终以应用页中的请求为准。URL 含地图 Key，对外分享或截图时先遮盖 `tk` 参数；无需导出包含学校凭据的 HAR。`favicon.ico` 的 404 与地图无关。

OSM 使用 WGS-84，天地图使用 CGCS2000，本项目在当前选点尺度下近似按 WGS-84 处理；学校所需 GCJ-02 仍沿用原有转换。地图不可用时可以手动输入坐标，无需重新完成学校登录。

## 构建下载与运行时出口

优先使用预构建镜像可避免服务器执行下列构建下载；镜像本身的拉取仍需要服务器访问 GHCR。源码构建各阶段独立，不应把一次 PyPI 200 当作“代理全通”：

| 阶段 | 实际发起方与目标 | 如何判断 |
| --- | --- | --- |
| 基础镜像 / 发行镜像拉取 | Docker daemon / BuildKit → 镜像仓库 | builder 的 RUN 代理参数不能自动解决拉取失败 |
| pip | Dockerfile RUN → PyPI 索引与 wheel 文件站点 | 索引 HEAD 成功不证明 wheel 大文件下载成功 |
| APT | `playwright install --with-deps` → 配置中的 Debian 软件源 | 包索引、依赖包与大包持续下载分别观察；APT 可能用 HTTP 与 `_apt` 用户 |
| Chromium | Playwright → 对应版本的浏览器下载 CDN | APT 完成不代表 Chromium CDN 可达；是独立下载 |
| 学校运行时 | 应用容器 → 学校固定服务 | 由 `UESTC_NETWORK_MODE` 与显式运行时代理决定 |
| 地图 | 用户浏览器 → 天地图 / OSM | 与服务器 build / 学校出口分别排查 |

先执行 `deploy/preflight.py`；工具不支持时不要进入反复 pip 下载。构建详细日志停在某个 APT 大包并不一定是死锁：先观察传输、持续速度和有界超时，短连接测速不代表长连接吞吐，不自动改源、关闭 TLS 或无限重试。

基础镜像可通过构建参数覆盖；默认值不固定为某一平台的摘要。使用镜像摘要时，由部署者核对来源、平台并安排更新。

```sh
# 示例：受信任的 Python 基础镜像引用由部署者提供。
PYTHON_IMAGE=your-trusted-registry/python:3.12-slim-bookworm docker compose --progress plain build

# 代理变量从当前 shell 传入构建；不要将含凭据的代理地址写进命令历史。
docker compose build --build-arg HTTP_PROXY --build-arg HTTPS_PROXY --build-arg NO_PROXY
```

Docker 的预定义构建代理参数用于 RUN 内的 pip / APT / Playwright 下载，不会自动解决 Docker daemon / BuildKit 拉取 `FROM` 镜像时的网络问题；后者使用部署者的 Docker 网络和镜像源配置。不要把代理写成 Dockerfile `ENV`、硬编码私网地址、默认使用 host 网络或关闭 HTTPS 校验。重复构建复用缓存，诊断时使用明文进度日志；日志和代理凭据均需保护。

上面的 `--build-arg HTTP_PROXY` 等只在构建命令使用；不要加入 Compose 的服务 `environment`、镜像 `ENV` 或公共 `.env.example`。源码构建安装器固定 `COMPOSE_BAKE=false`，减少环境意外启用 bake 的差异，但仍明确要求安装 buildx。构建完成后用 `up --no-build`，避免在没有构建代理参数时再次触发下载。维护 shell 中的代理变量按需 `unset`，示例见 [逐条部署命令](QUICKSTART.md#6-需要源码构建时)。

应用运行时默认 `UESTC_NETWORK_MODE=direct`，忽略系统代理。若访问学校必须经过出口代理，需在自己维护的 Compose 覆盖文件中显式添加 `HTTPS_PROXY` 并将模式改为 `system`。这是与镜像构建分开的配置，学校 TLS 校验始终启用。容器内 `127.0.0.1` 指向容器自身。

## 验证与日志

```sh
docker compose exec -T backend python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8090/healthz', timeout=2).status)"
docker compose logs --tail=100 backend
```

`/healthz` 不请求学校、不创建会话。接入代理后可在网页运行“离线自检”，实际学校登录和最终确认由本人操作。应用不记录密码、Cookie、token、请求正文、浏览器控制台或异常原文；容器日志最多 3 个 10 MB 文件。排错不要启用 HTTP trace、Playwright trace、HAR、录屏或打印整个环境。

<a id="更新回滚和会话维护"></a>

## 更新、回滚和会话维护

更新前等待正在执行的单次操作完成，记录当前 Git 提交、镜像标签及 Compose 项目名。默认项目名 `goodnightuestc`，数据卷通常为 `goodnightuestc_attempts`；已有部署使用其他名称时继续保留，避免无意创建空卷。新卷由镜像初始化为 UID/GID 10001 可写，不递归放宽现有业务数据权限。

```sh
# 当前开发分支；发行后可改为检出经过验收的固定版本标签。
git pull --ff-only origin web
# 编辑 .env，将 BACKEND_IMAGE 改为本次新的唯一标签。
docker compose config --quiet
docker compose build
docker compose up -d --wait
docker compose ps
```

保留 `.env` 的 `PUBLIC_ORIGIN`、网关密钥以及完整数据卷。重启清理内存工作台；浏览器包和设备 Cookie、密钥、恢复许可仍有效时可恢复学校登录，学校也可提前使会话失效。停止时最多等待已执行请求 125 秒，Compose 宽限期 140 秒。

回滚应用时，将 `BACKEND_IMAGE` 改回保留的旧镜像标签：

```sh
docker compose up -d --no-build --pull never --wait
docker compose ps
```

当前打包调整未变更数据库和会话包格式。今后若版本含数据迁移，按对应发行说明决定回滚兼容性；不要在已产生新提交后随意恢复旧防重复日志。清除学校会话会撤销当前设备恢复许可，重启后端则不会撤销仍有效的浏览器包。

```sh
docker compose stop
docker compose start
docker compose down
```

以上命令保留卷；**不要使用 `down -v`** 做停用或升级。长期停用时另行停用自己管理的代理入口。

### 一致性备份与恢复

先等待操作完成并停止后端，再完整备份 `/data`，包括密钥、两个 SQLite 数据库及可能的事务辅助文件；不要在线复制单个数据库。下例通过一次性容器读取同一个卷，备份文件只写到本机，不打印内容：

```sh
mkdir -p backups
chmod 700 backups
backup_file="backups/data-$(date +%Y%m%d-%H%M%S).tar.gz"
docker compose stop backend
(umask 077; docker compose run --rm --no-deps -T backend python -c "import sys, tarfile; archive = tarfile.open(fileobj=sys.stdout.buffer, mode='w|gz'); archive.add('/data', arcname='data'); archive.close()" > "$backup_file")
# 确认备份命令成功、备份可读后再启动。
docker compose start backend
```

另行保管 `.env` 和部署者的代理配置，限制读取权限。不要上传卷、备份、浏览器包或环境文件到 Git。恢复应在停止服务的情况下，将可信备份整体还原到一个空的恢复卷，再以 Compose 覆盖文件将 `attempts` 指向该卷；确认目录属主为 10001:10001、密钥 0600，先用备份对应版本启动验证，保留原卷以便撤回。不得把旧备份覆盖到仍在接收请求的卷中。服务器迁移还需保持相同 origin，浏览器才有机会恢复原包。

### 从内置网关版本迁移

新 Compose 不再包含 `gateway`，也不会自动停掉旧网关、删除证书卷或接管现有代理。曾使用内置 Caddy 的部署者，应先准备自己的 HTTPS 入口，再用旧 Compose 配置停止旧 `gateway`，随后切换后端配置；保留已有证书卷，不执行卷清理。已有宿主机 Nginx 或独立部署包的用户，只需核对完整 `PUBLIC_ORIGIN`（特别是端口）、网关密钥、项目名和数据卷对应关系，无需改动其证书与站点。

旧 `.env` 的 `SITE_DOMAIN` / `ACME_EMAIL` 不再使用，改填 `PUBLIC_ORIGIN`；旧 Nginx 端口覆盖文件通常不再需要，使用前先检查最终配置，避免叠加多个端口映射。升级仓库不会自动迁移服务器上的自定义文件。

更早的入口认证版本使用 `GS1` 会话包，当前 `GS2` 仅绑定站点和浏览器，升级后需重新完成一次学校登录。同步移除旧入口账号认证与身份头，使用 `MAX_SESSIONS_PER_BROWSER` 替代 `MAX_SESSIONS_PER_USER`，保留密钥、许可数据库与防重复日志。此前已使用 `GS2` 的部署，不因本次打包调整强制重登。

## 常见问题

| 现象 | 排查方向 |
| --- | --- |
| 健康正常，但页面 / API 返回 403 | 直连后端为预期；核对代理密钥、可信 IP 头、完整 Host 和非标准端口；写请求再核对 Origin / CSRF |
| HTTPS 入口不可达或证书不可信 | 属于部署者入口：检查 DNS、实际监听端口、证书链和代理就绪，后端不会自动修复 |
| `PermissionError` 或 `moonuestc.client` 导入失败 | `moonuestc` 是仓库内 vendor 模块，不要另行 pip 安装同名包。新镜像显式规范公开源码权限，并在非 root 下检查导入；外部测试只读挂载也需保证 UID 10001 可读和目录可遍历 |
| 构建拉取超时或 Chromium 下载慢 | 分别检查 daemon / BuildKit 拉取镜像网络和 RUN 内构建代理，见上文，不关闭 TLS |
| 页面正常但地图空白 / `ERR_CONNECTION_CLOSED` | 地图由浏览器直连；在失败设备检查图片域名、网络及 CSP，国内部署可配置天地图，见[地图配置与排障](#maps) |
| 天地图启动缺 Key 或图片加载失败 | 核对 `.env`、容器是否已重新创建，以及平台的地图服务权限、站点限制、配额和错误响应；不要公开含 Key 的请求 URL |
| 更新后出现全新空状态或无法恢复 | 核对卷名、项目名、origin、密钥和恢复许可是否仍来自原部署；不要删除数据库试错 |
| 429 / 登录资源不足 | 按并发调整工作台、登录和来源 IP 预算，检查实际 CPU / 内存；不能通过增加副本规避当前单实例约束 |

## 开发验证范围

```sh
python -m unittest discover -s tests -v
python tests/hosted_ui_smoke.py
python tests/session_ui_smoke.py
python tests/gateway_smoke.py
```

前两套浏览器测试使用合成学校页面、Cookie 和 mock；路由回调有有界超时，在关闭浏览器上下文前等待已进入回调结束，严格 CSP 下使用函数谓词等待，不放宽页面安全策略。外部页面请求被拦截，不访问学校。地图验证使用合成瓦片，覆盖天地图双图层、连接失败、重试及底图切换时坐标不变，不代表真实地图服务或 Key 已通过验证。`gateway_smoke.py` 使用标准库本地测试代理检查完整的非标准端口 origin、来源头覆盖、旧身份头清除、Secure Cookie、CSP、Host / Origin / CSRF 及直访限制；它不验证某个生产代理产品或公网 TLS。

CI `.github/workflows/docker.yml` 执行合成单元测试、Compose 校验、常规及严格 umask 构建上下文、非 root 只读容器、健康检查、Chromium 和上述浏览器及接入测试。生产镜像不包含测试、环境文件、截图或模拟数据库；测试文件以可读的只读挂载提供。所有这些检查均不代表真实学校 API 提交成功。实际代理、证书及本人学校流程由部署者分别验收，Firefox / Safari 也需目标设备验证。

安装器的回归检查使用临时目录、合成 Key 和 mock Docker 命令，覆盖已有配置及密钥保留、取消不改写、输入校验、文件权限与共享网络命令；不会为测试执行真实安装或修改服务器。CI 使用合成天地图 Key，不请求真实地图服务。

离线模拟器保留于源码，镜像不包含或启动它；本次没有增加定时提交。
