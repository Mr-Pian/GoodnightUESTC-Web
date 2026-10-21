# 晚安成电 · 测试组 Web 与本机签到

此仓库是 [`LCX-luo/GoodnightUESTC`](https://github.com/LCX-luo/GoodnightUESTC) 的 fork，当前开发仓库为 [`Mr-Pian/GoodnightUESTC-Web`](https://github.com/Mr-Pian/GoodnightUESTC-Web)。新增的 Docker 服务端提供独立用户会话、远程学校登录页面，以及 HTTPS 和测试组入口认证；浏览器可保存加密学校会话包，后端按需恢复到独立内存；服务器持久卷仅保存加密密钥、恢复许可元数据及最小提交尝试日志。部署步骤、配置、更新与回滚见 **[Docker 与自托管部署](deploy/README.md)**。

已有 Nginx 的服务器可参考 [Nginx 反向代理与端口配置](deploy/README.md#nginx)。Docker 模式默认在当前浏览器记住登录，优先使用 IndexedDB，失败时回退 localStorage；加密包最多保留 7 天，学校会话失效时需重登。服务器工作台仍按空闲 30 分钟／创建满 8 小时清理，但可从浏览器恢复。架构、兼容性、资源占用和次日登录规则见 [登录状态与有效期](deploy/README.md#sessions)。

下文的 Edge、加密本地文件和 `127.0.0.1` 说明适用于原有本机运行模式；Docker 模式以部署说明为准。

一个在本机运行的签到工具，提供 Windows 版和独立 Android 手机版：登录本人学校账号，在地图上选择实际位置，确认后向学校签到，再回读学校记录核对结果。

本机版本不需要服务器、Docker、Node.js、数据库安装或地图 API Key。Windows 版密码不落盘、Cookie 在本机加密保存；Android 版通过官方网页登录，会话保存在手机应用中。

**手机用户：** [下载 Android 安装包](downloads/GoodnightUESTC-Android.apk) · [Android 完整使用和编译说明](android/README.md)。Android 8.0+，手机独立运行，不需要电脑开机。当前为测试版，先使用“在线检查 / 只读预检查”确认流程。

## 免责声明

本工具仅供本人账号及学校允许的用途使用。请遵守学校管理规定，按本人实际位置签到；不得用于代签、伪造位置、批量操作他人账号或绕过学校验证。

点击最终确认会向学校提交真实签到记录，请自行核对内容及学校最终记录。本工具不保证持续可用或签到成功。密码不落盘，登录后仅保留在当前页面输入框；Cookie 在本机加密保存。请勿共用设备会话，或将私人数据上传到 GitHub、聊天或公开渠道。

## 快速开始

### 源码运行

只需要 **Python 3.10+** 和 **Microsoft Edge**。拉取本仓库后双击 `start.cmd`。

```powershell
git clone https://github.com/Mr-Pian/GoodnightUESTC-Web.git GoodNightUESTC
cd GoodNightUESTC
.\start.cmd
```

首次启动会自动创建独立 `.venv` 并安装四个直接依赖，随后自动打开 http://127.0.0.1:8090/ 。首次安装需要联网，后续启动复用环境。保持启动窗口运行；关闭窗口即停止本机服务。不要在公共电脑上保存会话。

也可手动运行：

```powershell
python bootstrap.py
```

首次安装后，项目使用自己的 `.venv`，不依赖其他项目的环境。

### 无 Python 的用户

维护者可分发 `GoodNightUESTC-Windows.zip`。用户解压整个目录，双击 `GoodNightUESTC.exe`；保留同目录的 `_internal` 文件夹。只需系统已有 Edge，不必安装 Python 或 pip 依赖。

本仓库提供 `build-windows.cmd` 和 GitHub Actions 构建流程，构建包默认不包含任何私人数据。可执行文件未经代码签名，Windows 可能提示未知发布者。当前仅验证 Windows，不宣称其他系统可直接运行。

## 使用流程

1. 在本机页面输入本人学号和学校统一认证密码，点击“用 Edge 登录并导入”。
2. 独立 Edge 窗口会自动填写官方登录页；本人按学校提示完成验证码、短信或二次验证。程序自动进入智慧学工、验证会话，随后加密保存 Cookie 并关闭窗口。不需要手动寻找 Cookie。
3. 在地图上点击本人实际位置，或输入经纬度，点击“保存位置”。名称用于本地辨认；确认地址从学校接口查询。
4. 点击“预检查并确认签到”。程序先查询当天记录、允许时段、位置范围和地址；不符合条件时停止，不发送签到。
5. 核对弹窗中的日期、地址、GCJ-02 坐标和时段，勾选本人实际在场的确认项，再点击“确认并提交签到”。
6. 程序发送一次签到请求，并回读学校当天记录。只有读到当天正常 `CHECKED_IN` 才显示“签到已确认”。

程序不会绕过学校 MFA，不会自动提交报告，也没有无人值守每日计划。学校要求重新认证时需本人重新登录。签到成功与否由学校系统判定；自动化测试使用合成数据和 mock，不能作为真实学校端提交成功的证明。

## 地图与坐标

使用 Leaflet 1.9.4 和 OpenStreetMap 底图，显示署名，正常使用浏览器缓存，不批量下载瓦片。

- 默认查看沙河校区，右上角可切换沙河与清水河；切换视图不会修改已保存的位置。
- 鼠标位于地图内时，滚轮缩放地图；地图外滚轮正常滚动页面。
- 地图 / GPS：WGS-84，点击地图后自动选择此坐标系。
- 高德：GCJ-02，手动输入时选择 GCJ-02。
- 学校接口使用 GCJ-02：WGS-84 做离线近似转换，GCJ-02 原样使用。GCJ-02 在 OSM 上展示时做近似反算。
- 地图加载需要联网；底图不可用时仍能手动输入坐标。
- 选点是用户配置，不代表实时 GPS。请求里的定位精度、定位耗时均为 0，网络类型标为 unknown，不伪称设备测量数据。

## 不上传打卡记录的测试

### 定时器离线模拟版

仓库附带 `scheduler_simulation.py`，用于先验证固定测试点、每日定时、进程常驻和本机防重复逻辑。它是独立的离线脚本，不导入签到模块或学校网络客户端；数据只写入 `private-data/simulation.sqlite3`，记录会标记为 `LOCAL_SIMULATION_ONLY`，**不会创建学校签到记录**。默认按运行机器的本地时区每天 21:30 触发，进程需要保持运行。

```sh
python scheduler_simulation.py --latitude 0 --longitude 0 --label "synthetic test point"
```

快速写入一条当天的本地模拟记录：

```sh
python scheduler_simulation.py --latitude 0 --longitude 0 --simulate-now
```

改用其他时间、查看记录或只检查一次当前时间：

```sh
python scheduler_simulation.py --latitude 0 --longitude 0 --daily-at 22:15
python scheduler_simulation.py --show
python scheduler_simulation.py --latitude 0 --longitude 0 --daily-at 22:15 --check-once
```

模拟器每天最多写一条本机记录；重复启动也不会在同一天重复写入。这里的坐标只是测试数据，不能作为已到场或学校签到成功的证明。

页面保留两个独立测试按钮，均不调用签到接口：

| 测试 | 访问学校 | 创建签到记录 |
| --- | --- | --- |
| 离线自检 | 否 | 否 |
| 在线只读检查 | 是，只允许固定 GET 路径 | 否 |
| 预检查并打开确认弹窗 | 是，只查询条件和地址 | 否 |
| 弹窗内“确认并提交签到” | 是 | 尝试创建，随后回读核验 |

**想测试而不上传记录，只运行前两个测试，或在确认弹窗中取消。**

离线自检检查坐标转换和诊断传输层的写入拦截，结果包含 `schoolRequests: 0`、`schoolWrites: 0`、`submitted: false`。在线只读检查显示学校当天已有记录、时段与所选位置是否在范围内；学校可能记录访问日志，但此操作不创建或修改签到记录。

`ReadOnlyTransport` 始终禁止业务 POST。实际签到使用单独的 `SubmissionTransport`，仅允许精确的签到路径、一次 POST，且只能在服务端确认令牌、本人位置确认、时段和围栏检查通过后使用。登录窗口允许学校认证请求，但不允许智慧学工业务写入。

### 自动测试

```powershell
.\test.cmd
```

现有合成数据测试覆盖：Cookie 加密、自动导入、取消登录、CSRF、只读请求拦截、坐标转换、确认令牌过期/重复使用、位置变更、围栏和时段限制、重复提交保护、提交后的记录回读，以及超时后的恢复。没有真实学校凭据，没有真实签到请求。

另有离线调度器测试，验证到点触发、日期防重复和模拟记录标记。

额外的 Edge 测试：

```powershell
.\.venv\Scripts\python.exe tests\ui_smoke.py
.\.venv\Scripts\python.exe tests\login_ui_contract.py
.\.venv\Scripts\python.exe app.py --self-test
```

- UI 测试在临时目录完成地图选点、保存、离线自检、模拟确认签到、刷新和手机布局检查；学校提交函数被模拟。
- 登录页测试只读取学校公开表单，阻断所有 POST，不输入账号密码。
- 运行时自检仅使用合成加密数据和空白 Edge 页面，可用于验证打包文件：`GoodNightUESTC.exe --self-test`。

这些检查不能代替本人账号的 MFA 和真实学校端验证。

## 重复提交与结果不明

每个账号每天的提交尝试会在发送前写入 `private-data/attempts.sqlite3`。当天有学校记录时不重复提交；本机当天已有提交尝试时只复查，不再 POST。请求超时、断线或进程重启后，先用“在线只读检查”核对学校记录，不自动重发。

界面中的“签到已确认”来自学校记录回读；接口返回成功本身不等于已确认签到。学校明确拒绝时显示业务码；不把请求不明当作成功。

## 私有数据

源码运行的数据位于项目 `private-data/`；可执行版本的数据位于 exe 同目录的 `private-data/`。

- `session.enc`：AES-GCM 加密的 Cookie、浏览器标识及账号。
- `dashboard.key`：本机加密密钥，不要与数据库/会话一起分享。
- `location.json`：用户选择的位置。
- `attempts.sqlite3`：本机按账号哈希与业务日期保存的提交尝试。

密码只在本次登录过程中使用，不写入配置或日志。本机模式保留页面密码输入框供用户自行编辑；Docker 模式发起登录后会清空该输入框。网页不返回 Cookie 原文。“清除本机会话”不会清除当天防重复记录，也不会影响你日常 Edge 的登录。

`.gitignore` 排除了私人数据、虚拟环境、日志和构建产物。不要手动把这些资料添加到 Git。服务只监听 `127.0.0.1:8090`，有 Host、Origin 和 CSRF 检查，不对公网开放。

## 构建与维护

双击 `build-windows.cmd`，或：

```powershell
python bootstrap.py --install-only
.\.venv\Scripts\python.exe -m pip install "pyinstaller>=6.0,<7"
.\.venv\Scripts\python.exe -m PyInstaller --noconfirm GoodNightUESTC.spec
```

产物为 `dist/GoodNightUESTC/`。分发时压缩整个文件夹；不要打包运行后生成的 `private-data/`。GitHub Actions 会运行合成测试并上传 Windows 构建产物，不读取学校账号或部署凭据。

项目结构：

```text
app.py                  本机服务、CSRF 与确认令牌
server.py               Docker 服务端入口，单进程多线程
hosted.py               独立内存会话、生命周期与限速
session_packages.py     浏览器会话包加密、设备绑定与恢复许可
static/session-*.js     浏览器保存、存储降级与按需恢复
Dockerfile / compose.yaml 容器构建和自托管服务
deploy/                 HTTPS 网关与部署说明
browser_login.py        Edge 登录和 Cookie 自动导入
core.py                 加密存储、坐标转换、只读诊断
checkin.py              手动提交、按日防重复和记录回读
network.py              直连/代理自动探测与安全切换
progress.py             操作阶段和等待时间
bootstrap.py            首次自动安装与启动
static/                 中文界面、地图和确认弹窗
tests/                  合成测试与 Edge 页面测试
scheduler_simulation.py 离线定时模拟器，不连接学校服务
vendor/moonuestc/       复用的上游协议/坐标/加密模块
```

## 来源与许可

本项目采用 MIT 许可。复用的上游模块保留原始许可和第三方声明；未包含上游的后台管理平台、CLI 或每日调度器。

- [MoonUESTC v0.5.1](https://github.com/horoscope963852/MoonUESTC)：学校协议、坐标与加密模块；许可见 `vendor/MoonUESTC-LICENSE`。
- [Playwright](https://playwright.dev/python/docs/browsers)：复用本机 Edge，无需下载另一套浏览器。
- [Leaflet](https://leafletjs.com/)：地图交互，许可见 `static/vendor/Leaflet-LICENSE`。
- [OpenStreetMap](https://www.openstreetmap.org/copyright)：地图底图。


## 连接速度与进度

学校请求和独立登录窗口默认自动选择路线：没有系统代理时直接连接；检测到代理时，优先快速直连并在必要时并行探测代理，缓存可用路线。系统代理开关或地址变化后重新选择。只读请求遇到网络失败可切换路线，真实签到 POST 永不自动重发。工具不会修改系统代理设置。可选地设置 `UESTC_NETWORK_MODE=direct` 或 `UESTC_NETWORK_MODE=system` 固定路线；旧的 `UESTC_USE_SYSTEM_PROXY=1` 仍兼容。

登录验证只读取一次学校记录；正常会话不再重复进入业务入口。确认签到复用已确认的地址，仍保留提交前的当天记录、位置范围和时段校验。页面显示具体阶段和已用时间。结果不明时只复查，不自动重发。


