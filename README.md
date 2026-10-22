# 晚安成电 选点签到-Web端

## **免责声明**

此仓库的相关内容仅用以技术交流, 核心API对接, 业务流程均源自上游仓库([`LCX-luo/GoodnightUESTC`](https://github.com/LCX-luo/GoodnightUESTC)), 本仓库仅为Web 服务器实现, 作者本人并未逆向过相关业务接口, 同时并不提供公用服务。 **请遵守学校管理规定，按本人实际位置签到**；不得用于**代签**、**伪造位置**、**批量操作他人账号或绕过学校验证**, 否则, **后果自负**!! 最终确认会向学校提交真实记录，请自行核对内容及学校最终记录。**本工具不保证持续可用或签到成功。**

## 项目描述

此仓库fork自[`LCX-luo/GoodnightUESTC`](https://github.com/LCX-luo/GoodnightUESTC) ，当前开发仓库为 [`Mr-Pian/GoodnightUESTC-Web`](https://github.com/Mr-Pian/GoodnightUESTC-Web)。本分支只维护 Docker / Web 部署。用户通过浏览器使用，服务器负责独立学校登录、请求代发、提交前检查和学校记录回读。正式入口为 `server.py`。

学校会话默认以加密包保存在用户浏览器，后端按需恢复到独立内存。服务器持久卷保存加密密钥、恢复许可元数据和最小提交尝试日志，不保存学校密码或学校 Cookie 快照数据库。

## 项目结构

```text
server.py                正式入口，单进程多线程 Waitress
app.py                   Web API、网关校验、CSRF 与确认令牌
hosted.py                独立内存工作台、生命周期与限速
session_packages.py      会话包加密、设备绑定与恢复许可
browser_login.py         服务器 Chromium 登录和会话导入
core.py                  学校会话验证、坐标转换与只读诊断
checkin.py               单次提交、按日防重复与学校记录回读
network.py / progress.py 网络路线、超时与操作进度
static/                  Web 界面、地图、远程登录和浏览器保存
Dockerfile / compose.yaml 容器构建与自托管服务
deploy/                  交互安装器、部署说明、可选共享代理网络配置
tests/                   合成测试与 Web 界面测试
scheduler_simulation.py  独立离线模拟器，不连接学校
vendor/moonuestc/        复用的协议、坐标与传输模块
```

学校请求默认由容器直连；如需出口代理，按部署文档显式配置。只读请求可在网络失败后切换路线，真实签到 POST 不自动重发。页面显示操作阶段和已用时间，结果不明时应先只读核对。

## 部署入口

准备可访问学校官方服务的 Linux 服务器、Docker Engine 和 Compose v2。**Docker 只启动应用**，包含网页、后端和登录所需 Chromium；公网域名、反向代理及 HTTPS 证书由部署者自行接入。安装、配置、更新、回滚和备份见 **[后端部署说明](deploy/README.md#后端安装与启动)**。

拉取源码后，推荐在服务器交互终端运行：

```sh
python3 deploy/install.py
```

安装器只需要宿主机 Python 3.8+ 标准库，不要求在宿主机安装应用依赖。它询问完整 HTTPS 地址和**运维自己申请的天地图 Key**（输入不回显，不能为空），生成缺失的内部代理密钥，将配置保存到权限 `0600` 的 `.env`，然后构建并等待后端健康。再次运行可回车保留已有地址和 Key，内部密钥和其他自定义配置保留。只生成配置可加 `--configure-only`；已有容器代理网络可加 `--proxy-network 网络名`，不会创建网络或修改代理。CI / 无人值守部署可直接维护 `.env` 并运行 Compose。

- 默认仅映射 `127.0.0.1:18090 → 8090`，供现有宿主机代理连接，不绑定公网 80/443、不申请证书。
- 已有容器代理可使用 [共享网络覆盖文件](deploy/compose.proxy-network.yaml)，访问 `backend:8090` 并取消宿主机端口映射。
- `PUBLIC_ORIGIN` 显式填写完整 HTTPS origin，支持非标准端口，例如 `https://checkin.example.com:60000`。按照[代理接入约定](deploy/README.md#proxy-contract)配置头、超时和缓存；未接入代理时，后端 `/healthz` 返回 200、普通页面与 API 返回 403 属于预期。

网页不设置额外的网站账号或 HTTP Basic Auth；用户直接完成学校统一认证。`GATEWAY_SECRET` 用于反向代理与后端之间的内部校验，由运维配置，用户无需输入。后端采用单进程、单副本、多线程模式，不支持直接增加多个 WSGI worker 或后端副本。目前提供源码构建，未发布官方预构建镜像；发行镜像和 Release 可在版本验收后另行发布。

## 使用流程

1. 直接访问部署好的 HTTPS 站点。
2. 在页面输入本人学号和学校统一认证密码，点击“打开学校登录页面”。密码框在发起登录后清空。
3. 学校登录在服务器的独立 Chromium context 中进行，页面显示其交互画面。本人按学校提示完成验证码、短信或二次验证；学校会话验证成功后关闭 Chromium。
4. 默认开启“在此浏览器记住学校登录”。保存成功后可跨页面和后端重启恢复；共用设备请关闭，并在使用后清除学校会话。
5. 在地图选择本人实际位置，或输入经纬度，点击“保存位置”。位置只保存在当前服务器工作台，工作台重建后需要重新选择。
6. 点击“预检查并确认签到”。程序查询当天记录、允许时段、位置范围和地址；条件不满足时停止。
7. 核对弹窗中的日期、地址、GCJ-02 坐标和时段，勾选本人实际在场的确认项，再点击“确认并提交签到”。
8. 程序发送一次签到请求，并回读学校当天记录。只有读到当天正常 `CHECKED_IN` 才显示“签到已确认”。

程序不自动完成学校二次验证，也没有无人值守的每日提交计划。学校要求重新认证时需本人重新登录。自动化测试使用合成数据与 mock，其通过不能作为真实学校 API 提交成功的证明。

## 会话架构与有效期

学号、密码、验证码经 HTTPS 发到后端，供本次学校登录使用；学校 Cookie 也由后端在使用时处理。每个工作台有独立的会话、CookieJar、操作锁、进度、CSRF 和确认令牌，不能跨成员共用浏览器 profile 或认证上下文。登录完成后不长期运行学校浏览器。

后端以 AES-256-GCM 加密学校 Cookie、学号、User-Agent 和保存时间，并绑定站点与随机的 HttpOnly 浏览器设备 Cookie。前端优先用 IndexedDB 保存，失败或超时后回退 localStorage。再次打开时，浏览器上传加密包；后端检查绑定、期限和恢复许可，执行一次学校只读验证，然后恢复到隔离内存。恢复不启动 Chromium，不使用学校密码。浏览器标识只用于隔离与绑定，不代表学校身份；来源 IP 只用于限速，不参与会话包绑定，因此切换网络可继续恢复。

| 内容 | 用户浏览器 | 服务器 |
| --- | --- | --- |
| 学校密码、验证码 | 输入并经 HTTPS 发送；发起登录后清空页面密码框 | 仅本次登录临时处理，不保存到会话包或业务日志 |
| 学校 Cookie、学号、User-Agent | 保存加密会话包，不接收明文学校 Cookie | 当前工作台内存中处理，学校请求各使用独立 CookieJar |
| 工作台和设备标识 | Secure / HttpOnly / SameSite=Strict Cookie | 用于工作台隔离和包的设备绑定 |
| 实际位置、待确认操作 | 页面显示当前选择 | 当前工作台内存，不放入会话包 |
| 防重复与恢复许可 | 不负责全局防重复 | 持久卷中的最小日志、许可元数据与加密密钥 |

今天登录、明天用同一浏览器打开，默认会尝试自动恢复，但有效期由以下条件共同决定：

- 加密包默认从首次签发最多 **7 天**。恢复、轮询和学校 Cookie 更新不延长此期限；重新完成学校登录才签发新期限。
- 服务器工作台默认 **空闲 30 分钟**或**创建满 8 小时**后清理；仍有效的浏览器包可重新恢复。前台状态轮询算作活动。
- 学校 Cookie 到期、学校服务端策略或学校主动撤销可以让会话提前失效，此时需重登。
- 清理站点数据、结束无痕会话、更换浏览器／设备／域名、丢失服务器密钥或恢复许可后，可能无法恢复。

“清除学校会话与本机保存”会清除当前学校内存、本机包并撤销设备恢复许可；取消记住登录会撤销保存，但当前内存工作台仍可继续使用。两者均不删除当天防重复日志，也不注销学校其他设备。

浏览器每包最多 64 KiB，当前设备的新包替换旧包，本地存储最多保留 8 个命名空间的记录；服务端默认最多 64 个工作台、每浏览器标识 8 个、并行登录浏览器 2 个。请求同时按浏览器和可信代理提供的来源 IP 限速。Chrome / Edge / Firefox / Safari 所需存储接口均有支持，但隐私设置与自动清理策略可能阻止保存；页面会显示实际保存结果。学校失效与网络超时分别处理，网络故障不会直接删除本机包。详细限制见[登录状态与有效期](deploy/README.md#sessions)及部署文档中的兼容性、资源占用说明。

从带有入口认证的旧版升级后，原会话包与旧入口身份绑定，需重新完成一次学校登录；此后保存的包使用浏览器绑定。请按[升级说明](deploy/README.md#更新回滚和会话维护)同步更新代理配置，并保留原数据卷。

## 数据卷与重复提交

Docker 的 `attempts` 卷包含：

| 文件 | 用途 |
| --- | --- |
| `attempts.sqlite3` | 学号哈希、业务日期、提交尝试结果与时间，用于跨工作台和重启防重复 POST |
| `session-package.key` | 会话包加密密钥，首次启动生成，必须保留并保护 |
| `session-packages.sqlite3` | 恢复许可、浏览器设备的 HMAC 标识、签发与到期时间 |

同一学校账号同一天最多允许一次进入发送阶段的尝试，限制覆盖不同浏览器、不同工作台和后端重启。学校当天已有签到、报告或豁免记录时跳过提交。发送前先在 SQLite 中争抢唯一记录，网络超时、学校拒绝或结果不明后均不自动释放重发机会；后续只复查记录。不要通过删除卷或日志解除该限制。

确认令牌仅用于当前工作台、账号、位置和当天，120 秒内有效且只能消费一次；提交还要求本人实际位置确认、操作互斥、时段和围栏检查。接口返回成功本身不等于已确认签到，以学校记录回读为准。

日常更新保留整个数据卷及旧镜像；不要使用 `docker compose down -v`。服务器与 HTTPS 终止代理都必须可信，日志不得记录密码、Cookie、token 或请求正文。卷、备份、浏览器加密包也不应上传到 Git 或公开渠道。完整备份与回滚步骤见[部署文档](deploy/README.md#更新回滚和会话维护)。

## 地图与坐标

使用随应用提供的 Leaflet 1.9.4，支持**天地图官方 HTTPS 瓦片**和 OpenStreetMap。国内部署建议配置天地图；不加载额外地图 SDK，不要求 WebGL。天地图叠加矢量底图与中文注记两层，浏览器按当前视图加载并正常使用 HTTP 缓存，不批量下载或预取离线地图。

在[天地图开发资源](https://lbs.tianditu.gov.cn/)申请自己的、可用于网页调用地图服务的 Key，按平台规则设置站点限制与配额。推荐安装器会询问并保存 Key；手动部署则在服务器 `.env` 填写：

```dotenv
MAP_PROVIDER=tianditu
TIANDITU_KEY=填写自己的天地图Key
```

更新到包含此功能的代码后，运行 `docker compose up -d --build backend`，然后刷新网页。天地图的 Key 会出现在浏览器配置和瓦片请求中，属于浏览器可见的地图调用凭据，应使用专用 Key 并配置平台限制；不要填入学校密码、学校 Cookie 或内部 `GATEWAY_SECRET`，也不要把真实 `.env` 提交到仓库。

| 配置 | 默认底图与行为 |
| --- | --- |
| `MAP_PROVIDER=tianditu`（安装器与 `.env.example` 推荐值） | 默认天地图；缺少 Key 时启动报错，避免配置遗漏后静默使用 OSM |
| `MAP_PROVIDER=auto`（未设置变量时的兼容值） | 有 `TIANDITU_KEY` 时优先天地图，没有时保持旧版 OpenStreetMap |
| `MAP_PROVIDER=osm` | 默认 OpenStreetMap；配置了天地图 Key 时仍可在页面切换到天地图 |

“地图底图”下拉框用于真正切换地图服务；“输入坐标系”只说明经纬度的来源。底图选择只作用于当前页面，不修改输入值、标记或已保存位置。加载失败会显示提示及“重试底图”；不会自动反复重试或在网络失败后自行切换服务。未配置天地图 Key 时，页面只提供 OSM。

- 默认查看沙河校区，右上角可切换沙河与清水河；切换视图不会修改已保存位置。
- 地图内滚轮缩放地图，地图外滚轮正常滚动页面。
- OSM / GPS 使用 WGS-84；天地图使用 CGCS2000，本项目在当前地图选点尺度下近似按 WGS-84 处理，两者并非严格相同的测量坐标系。点击两种底图均记录为 WGS-84；来自高德的坐标应选择 GCJ-02。
- 学校接口使用 GCJ-02：WGS-84 做离线近似转换，GCJ-02 原样使用；手工输入 GCJ-02 时，在两种底图上均近似反算展示，避免重复偏移。
- 底图不可用时仍可手动输入坐标。选点是用户配置，不代表实时 GPS；请求中的定位精度、定位耗时为 0，网络类型标为 unknown。

地图图片由**用户浏览器直接访问地图服务商**，不经过后端；Docker 不包含地图代理，不需要增加端口或数据卷。服务商可以看到浏览器 IP、站点来源和浏览区域，学校凭据不会随这些跨域瓦片请求发送。后端 CSP 仅放行已支持的图片域名，脚本继续限制为本站。若反向代理另外设置 CSP，也需允许配置后的地图图片来源，详见[地图配置与排障](deploy/README.md#maps)。

朋友设备出现 `tile.openstreetmap.org` 的 `ERR_CONNECTION_CLOSED` 时，应检查该设备到地图服务的连接；服务器能访问不能代表浏览器能访问。可以在失败设备直接打开 Network 中的同一图片 URL 对照。`favicon.ico` 的 404 与地图无关。天地图接入以[官方地图服务文档](https://lbs.tianditu.gov.cn/server/MapService.html)为依据，保留来源署名；Key、配额和各设备的真实网络仍需部署者实测。

## 不创建学校记录的检查

| 操作 | 访问学校 | 创建签到记录 |
| --- | --- | --- |
| 离线自检 | 否 | 否 |
| 在线只读检查 | 是，只允许固定 GET 路径 | 否 |
| 预检查并打开确认弹窗 | 是，只查询条件和地址 | 否 |
| 弹窗内“确认并提交签到” | 是 | 尝试创建，随后回读核验 |

离线自检包含坐标转换和诊断传输层写入拦截，结果显示 `schoolRequests: 0`、`schoolWrites: 0`、`submitted: false`。在线只读检查不创建或修改签到记录，学校仍可能记录访问日志。测试时可停在预检查弹窗并取消。

`ReadOnlyTransport` 禁止业务 POST；实际签到的 `SubmissionTransport` 只允许精确签到路径的一次 POST。学校登录页面允许认证请求，仍禁止智慧学工业务写入。

## 开发与测试

开发验证使用 **Python 3.12**，与 Docker 镜像一致：

```sh
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
```

合成测试覆盖学校会话验证、登录取消、多用户隔离、网关与 CSRF、限速、工作台到期、加密包和设备绑定、确认令牌、坐标与围栏、重复提交、超时与学校记录回读。离线模拟器的日期防重复测试也保留。

托管界面冒烟测试默认使用 Playwright Chromium：

```sh
python -m playwright install --with-deps chromium
python tests/hosted_ui_smoke.py
python tests/session_ui_smoke.py
```

`hosted_ui_smoke.py` 检查隔离用户、远程登录画面与输入、密码清空、天地图底图与注记、瓦片失败和重试、切换底图时坐标不变、GCJ-02 展示反算、地图选点、模拟确认提交、离线自检和手机布局。`session_ui_smoke.py` 检查浏览器保存、后端重启／工作台清理后的恢复、存储降级与清除。两者拦截外部页面请求，学校操作均使用合成快照或 mock，不访问真实学校；地图也使用合成图片，不能证明真实地图服务在各设备上可达。开发时可用 `TEST_BROWSER_CHANNEL` 指定已安装的兼容浏览器通道；这不影响生产容器使用 Chromium。

`python tests/gateway_smoke.py` 使用标准库本地测试代理验证非标准端口 Host / Origin、伪造代理头覆盖、Cookie / CSP、CSRF 及后端直连拦截，无需安装 Nginx / Caddy，也不申请证书。`tests/login_ui_contract.py` 是单独手动运行的学校公开登录表单兼容检查：只读取公开页、阻断 POST，不输入凭据；不属于离线测试或 CI。

唯一 CI 为 `.github/workflows/docker.yml`，验证合成单元测试、Compose、常规与严格 umask 镜像构建、容器健康、代理接入约定、Chromium 和两个托管界面流程。生产镜像不包含测试文件，CI 以可读的只读挂载方式运行界面测试。

## 独立离线模拟器(弃用)

`scheduler_simulation.py` 用于验证每日触发、进程常驻与本地防重复；它不导入学校客户端，不连接学校，不创建学校签到记录，也不在 Docker 镜像中启动。默认按运行机器本地时区每天 21:30 触发，需要保持进程运行；记录写入 `private-data/simulation.sqlite3` 并标记 `LOCAL_SIMULATION_ONLY`。

```sh
# 持续运行，或立即写入当天一条模拟记录。
python scheduler_simulation.py --latitude 0 --longitude 0 --label "synthetic test point"
python scheduler_simulation.py --latitude 0 --longitude 0 --simulate-now

# 修改时间、只检查一次、查看模拟记录。
python scheduler_simulation.py --latitude 0 --longitude 0 --daily-at 22:15
python scheduler_simulation.py --latitude 0 --longitude 0 --daily-at 22:15 --check-once
python scheduler_simulation.py --show
```

这些坐标与记录仅为测试数据，不能作为已到场或学校签到成功的证明。

## 项目许可

本项目采用 MIT 许可，复用模块保留原始许可和第三方声明；未包含上游的后台管理平台、CLI 或每日调度器。

- [MoonUESTC v0.5.1](https://github.com/horoscope963852/MoonUESTC)：协议、坐标与传输模块；许可见 [vendor/MoonUESTC-LICENSE](vendor/MoonUESTC-LICENSE)。
- [Playwright](https://playwright.dev/python/docs/browsers)：服务器独立 Chromium 登录与合成界面测试。
- [Leaflet](https://leafletjs.com/)：地图交互，许可见 [static/vendor/Leaflet-LICENSE](static/vendor/Leaflet-LICENSE)。
- [天地图](https://www.tianditu.gov.cn/)与 [OpenStreetMap](https://www.openstreetmap.org/copyright)：可选地图底图。
