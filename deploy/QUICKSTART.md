# 固定版本 Docker 部署：从命令行到验收

本文以 **v1.0.5 / Linux amd64** 为例。镜像为 `ghcr.io/mr-pian/goodnightuestc-web:1.0.5`，需要对应 Release 工作流成功，且 GHCR 包允许公开拉取。以下域名、端口和网络名称均为示例，不是作者提供的公共服务。项目只交付应用，不自动配置 Nginx、域名或证书。

## 1. 准备主机与工具

推荐独立 Linux 主机 2 核 / 4 GiB 内存，至少预留 10 GiB 磁盘；2 GiB 小主机需减少并行登录并观察余量。具体预算见 [资源规划](RESOURCES.md)。镜像目前只发行 Linux amd64，ARM 主机请用源码构建并自行验收。

先确认系统和架构：

```sh
uname -m
cat /etc/os-release
```

已有 Docker 的主机跳过安装，直接进行下方能力检查。Ubuntu / Debian 全新主机可参考以下 Docker 官方 APT 源安装方式；其他发行版按 [Docker 官方安装文档](https://docs.docker.com/engine/install/)操作。已有容器业务时先确认包兼容与维护窗口，不批量卸载现有 Docker / containerd。

以下命令在管理员 shell 中执行，后文沿用该 shell；也可由运维逐条使用 `sudo`。Docker 权限等同高权限，不将 socket 改为 `0666`。

```sh
sudo -i
apt-get update
apt-get install -y ca-certificates curl python3
install -m 0755 -d /etc/apt/keyrings
. /etc/os-release
case "$ID" in ubuntu|debian) ;; *) echo '本段仅支持 Ubuntu / Debian，请改用对应发行版官方安装方式。'; exit 1 ;; esac
curl -fsSL "https://download.docker.com/linux/$ID/gpg" -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc
cat > /etc/apt/sources.list.d/docker.sources <<EOF
Types: deb
URIs: https://download.docker.com/linux/$ID
Suites: ${UBUNTU_CODENAME:-$VERSION_CODENAME}
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker.asc
EOF
apt-get update
apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin docker-buildx-plugin
systemctl enable --now docker
docker version
docker compose version
docker buildx version
python3 --version
```

要求：Docker CLI 和 Engine 均 >= 24.0.0；Compose >= 2.20.0；已有容器代理的共享网络模式需要 Compose >= 2.24.4。源码构建还要求 buildx >= 0.12.0、可访问当前 builder，且不能设置 `DOCKER_BUILDKIT=0`。预构建镜像运行不要求 buildx。宿主机安装器仅需 Python 3.8+ 标准库。

## 2. 获取固定版本并校验附件

全新安装示例。已有实例请先看第 7 节，保留原项目名、origin、内部密钥及数据卷，不能把以下新建目录步骤直接用于覆盖现有部署。

```sh
RELEASE_VERSION=1.0.5
install -m 0750 -d /opt/goodnightuestc
cd /opt/goodnightuestc
RELEASE_BASE="https://github.com/Mr-Pian/GoodnightUESTC-Web/releases/download/v${RELEASE_VERSION}"
curl -fL --retry 3 -O "${RELEASE_BASE}/goodnightuestc-web-${RELEASE_VERSION}.tar.gz"
curl -fL --retry 3 -O "${RELEASE_BASE}/DEPLOYMENT.md"
curl -fL --retry 3 -O "${RELEASE_BASE}/SHA256SUMS"
sha256sum -c SHA256SUMS
tar -xzf "goodnightuestc-web-${RELEASE_VERSION}.tar.gz"
cd "goodnightuestc-web-${RELEASE_VERSION}"
python3 deploy/preflight.py --image-only
```

校验文件用于检查下载完整性，应从项目的可信 HTTPS Release 页面获取；它不是独立数字签名。若出现 404，先确认对应版本的 Release 与附件已成功发布，不静默改测旧版本。

## 3. 输入自己的配置并启动镜像

先在 [天地图](https://lbs.tianditu.gov.cn/)申请自己的地图服务 Key，为实际访问站点配置来源白名单。Key 与完整 HTTPS 地址只在自己的终端输入，Key 不回显。

```sh
python3 deploy/install.py --image ghcr.io/mr-pian/goodnightuestc-web:1.0.5
```

安装器先检查工具，然后询问 `PUBLIC_ORIGIN`（例如 `https://checkin.example.com:60000`）和 Key，原子写入权限 `0600` 的 `.env`，执行配置校验、拉取镜像和 `up --no-build --wait`。它不打印 Key 和内部密钥，不向镜像仓库上传 `.env`。没有 buildx 的服务器也能采用此方式。

已有容器代理网络时，改用这一条；网络应已由运维建立并接入自己的代理：

```sh
python3 deploy/install.py --image ghcr.io/mr-pian/goodnightuestc-web:1.0.5 --proxy-network my-proxy-network
```

后续所有 Compose 命令须一致使用 `docker compose -f compose.yaml -f deploy/compose.proxy-network.yaml …`，重复安装也继续带该网络参数。共享网络模式取消宿主机端口映射，代理访问 `http://backend:8090`；默认模式为宿主机代理访问 `http://127.0.0.1:18090`。

仅配置、稍后再启动：

```sh
python3 deploy/install.py --configure-only --image ghcr.io/mr-pian/goodnightuestc-web:1.0.5
docker compose config --quiet
docker compose pull backend
docker compose up -d --no-build --wait backend
```

不要在 `.env` 之外另行 export 同名设置后混用手动 Compose 命令，shell 值会优先于 `.env`。安装器会过滤它管理的同名覆盖；手动命令需自行确认。不要直接输出完整 Compose 配置或 `.env` 来排错。

## 4. 验证应用容器

默认宿主机代理模式：

```sh
docker compose config --services
docker compose ps
docker compose port backend 8090
curl --fail --max-time 5 http://127.0.0.1:18090/healthz
curl --silent --output /dev/null --write-out '%{http_code}\n' --max-time 5 http://127.0.0.1:18090/api/status
docker compose exec -T backend id
docker stats --no-stream "$(docker compose ps -q backend)"
```

预期仅 `backend`；仅回环端口；健康 200；没有可信代理头直连 `/api/status` 返回 403；UID/GID 为 10001。共享网络模式跳过宿主机端口和两条 curl，使用容器内检查：

```sh
docker compose -f compose.yaml -f deploy/compose.proxy-network.yaml exec -T backend python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8090/healthz', timeout=2).status)"
```

## 5. 接入自己的 HTTPS 反向代理

应用部署在站点根路径，不能挂在 `/goodnight/` 之类子路径。公网只开放运维实际选择的 HTTPS 端口；8090 和默认 18090 不应额外暴露公网。证书续期、DNS、CDN / NAT、端口转发和防火墙均由运维管理。

Nginx 参考片段如下，放入已有 HTTPS `server`，替换示例 Host 与内部密钥。密钥在本机安全编辑器中从 `.env` 配对，不粘贴到聊天、工单或公开配置。该片段不创建证书：

```nginx
location / {
    proxy_pass http://127.0.0.1:18090;
    proxy_http_version 1.1;
    proxy_set_header Host checkin.example.com:60000;
    proxy_set_header X-Team-Gateway REPLACE_WITH_LOCAL_GATEWAY_SECRET;
    proxy_set_header X-Client-IP $remote_addr;
    proxy_set_header X-Team-User "";
    proxy_set_header Authorization "";
    client_max_body_size 96k;
    proxy_connect_timeout 10s;
    proxy_read_timeout 150s;
    proxy_send_timeout 150s;
    proxy_next_upstream off;
    proxy_buffering off;
    proxy_request_buffering off;
    proxy_cache off;
    access_log off;
}
```

Host 必须等于 `PUBLIC_ORIGIN` 的 authority，非标准端口不能省略；默认 443 则不额外写示例 60000。容器代理应将 upstream 改为共享网络的服务名。前面还有 CDN 或其他代理时，先配置可信来源的 real IP 规则，不能直接信任任意 `X-Forwarded-For`。详见 [代理约定](README.md#proxy-contract)。

```sh
nginx -t
systemctl reload nginx
```

在浏览器使用自己的实际 HTTPS 地址检查页面、天地图底图和注记、离线自检、本人学校登录及保存恢复。离线自检不提交学校记录。地图空白时先按 [天地图来源白名单排错](README.md#maps)核对浏览器来源；不要导出带凭据的 HAR。

## 6. 需要源码构建时

源码包和 GitHub 仓库包含全部构建文件。构建前执行：

```sh
python3 deploy/preflight.py
python3 deploy/install.py --configure-only
```

在本机编辑 `.env`，将 `BACKEND_IMAGE` 设为自己的唯一标签（如 `goodnightuestc-web:1.0.5-local`），然后：

```sh
COMPOSE_BAKE=false docker compose --progress plain build backend
docker compose up -d --no-build --wait backend
```

交互一键源码构建也可直接执行 `python3 deploy/install.py`。运行环境、图片仓库拉取、pip、APT、Chromium 下载是不同检查，参考 [构建网络](README.md#构建下载与运行时出口)。安装器工具预检不承诺这些网络均可达。

构建代理仅放在当前维护 shell，输入不进入命令历史；以下为 Bash 命令：

```bash
read -r -s -p '构建 HTTP/HTTPS 代理 URL: ' BUILD_PROXY_URL
printf '\n'
export HTTP_PROXY="$BUILD_PROXY_URL"
export HTTPS_PROXY="$BUILD_PROXY_URL"
export NO_PROXY=localhost,127.0.0.1
COMPOSE_BAKE=false docker compose --progress plain build --build-arg HTTP_PROXY --build-arg HTTPS_PROXY --build-arg NO_PROXY backend
unset BUILD_PROXY_URL HTTP_PROXY HTTPS_PROXY NO_PROXY
docker compose up -d --no-build --wait backend
```

不要在之后再次使用没有这些构建参数的 `up --build` 误触发重建。代理必须从实际 builder 网络可达，`127.0.0.1` 通常指构建容器自身。默认不启用 host 网络；只有部署者确认需要时才在自己的覆盖文件中设置 **build.network**，运行时仍保持隔离网络和 direct。代理 URL 若含凭据，构建错误日志也应私有保管。

## 7. 更新、回滚与停止

保存原 `.env`、项目名、所有代理覆盖文件和完整数据卷；将旧镜像固定到版本标签或摘要。先按 [整卷备份步骤](README.md#更新回滚和会话维护)在维护窗口备份，不以更新为由清除日志或密钥。

更新前先解压匹配的新版本包，并在私有位置迁移现有 `.env` 和覆盖文件，保持 `COMPOSE_PROJECT_NAME` / Compose 项目名不变。若原项目未显式设置项目名，先查 `docker compose ls` 和原容器标签后在 `.env` 固定它，否则换目录可能启用新卷。安装器只需改为新的版本镜像。

在已经配置好的同一实例中，仅更新镜像时：

```sh
# 用本机编辑器把 .env 的 BACKEND_IMAGE 改为待升级的固定版本。
docker compose config --quiet
docker compose pull backend
docker compose up -d --no-build --wait backend
docker compose ps
```

回滚时在 `.env` 恢复**已经保留的旧镜像引用**，继续：

```sh
docker compose up -d --no-build --pull never --wait backend
```

若版本还改变 Compose 或数据库格式，应同时按该版本说明恢复兼容配置。不要用旧数据库覆盖升级后新产生的防重复记录。停止保留卷：

```sh
docker compose stop backend
```

不执行 `down -v`、清理业务卷或 `docker system prune`。运行时环境变量修改需要重新创建容器，`restart` 不读取新 `.env`。密钥更新、域名变化和学校主动失效都可能影响浏览器保存恢复。
