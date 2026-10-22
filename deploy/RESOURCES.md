# 服务器资源规划与测量

以下数字是小型部署的**预算与观察起点**，不是本项目的实测峰值、最低保证或承载人数结论。现有部署反馈只有约 2 GiB 主机上运行并完成页面与地图验证的证据，没有空闲 / 登录峰值的完整采样，也没有压力测试。

| 场景 | 建议预留 | 说明 |
| --- | --- | --- |
| 推荐独立主机 | 2 vCPU、4 GiB RAM、可用磁盘 10 GiB 以上 | 给 Chromium 瞬时峰值、系统和更新留出余量 |
| 小型受限主机 | 2 vCPU、2 GiB RAM | 建议并行登录 1；若共存其他业务，必须实测剩余内存，不能把容器 2 GiB 上限当作主机预算 |
| 后端空闲预算 | 约 100–300 MiB | Waitress、Python、模块和少量工作台；需实测，不是实测值 |
| 一次学校登录额外预算 | 约 300–700 MiB，CPU 短时可接近一核或更多 | Chromium 启动、远程页面和截图；页面内容及验证方式会改变峰值 |
| 默认两次并行登录预算 | 为应用预留约 1–2 GiB 并观测峰值 | 不承诺 2 GiB 内永不 OOM；必要时减并发 |
| 不同用户保持已登录 | 主要为小型 Python 会话与 CookieJar | 登录完成即关闭 Chromium；64 个工作台上限不代表可承载 64 人同时登录 |
| 镜像 / 构建 / 回滚 | 规划 5–10 GiB 可用空间 | 发行镜像含 Chromium 与系统依赖，压缩下载大小、解压层和构建缓存各不相同；保留当前和旧镜像 |

默认 Compose 上限：CPU 2、内存 2 GiB、PID 256、`/tmp` tmpfs 上限 512 MiB、共享内存 256 MiB；应用并行登录最多 2。tmpfs / shm 按实际写入消耗内存，并非启动即预留，也不能与应用 RSS 简单相加推断峰值；容器内存上限仍需涵盖实际使用。宿主机 swap 不是 RAM 的性能替代品，也可能将运行时敏感数据换出到磁盘。

运行时只有一个后端进程、8 个 Waitress 线程，不扩成多 worker 或多副本。SQLite 防重复与恢复元数据容量受使用天数、人数和设备数影响，不能承诺永不增长。容器日志默认轮转为 3 × 10 MB。地图由浏览器直连提供商，不占后端地图带宽；登录交互的截图与轮询才会增加后端带宽。

## 小主机调整示例

以下是可选起点，先评估该主机其他业务；不要在已有 `.env` 不知情时重复追加同名配置。在本机编辑 `.env` 的已有值：

```dotenv
MAX_CONCURRENT_LOGINS=1
MAX_SESSIONS=32
```

如需容器内存上限 1 GiB，由运维创建私有 `compose.resources.yaml`：

```yaml
services:
  backend:
    mem_limit: 1g
    memswap_limit: 1536m
```

在有合适 swap 的主机上，这代表 RAM 上限 1 GiB、RAM + swap 总上限 1.5 GiB，**不是额外 1.5 GiB swap**。内存更紧可能导致 Chromium 被 OOM 杀死；这不构成可用性保证。无 swap、远程容器环境或不同内核的行为需实测。

```sh
docker compose -f compose.yaml -f compose.resources.yaml config --quiet
docker compose -f compose.yaml -f compose.resources.yaml up -d --no-build --wait backend
```

后续始终保留相同 `-f` 文件组合；共享代理网络部署还需同时带网络覆盖文件。

## 无凭据的采样命令

分别在启动后空闲、学校登录进行中、登录完成关闭 Chromium 后观察；真实登录由本人操作，采样不打印页面、Cookie 或请求正文。

```sh
free -h
df -h
docker system df
docker compose images
docker stats --no-stream "$(docker compose ps -q backend)"
docker inspect --format 'OOMKilled={{.State.OOMKilled}} Restarts={{.RestartCount}} MemoryLimit={{.HostConfig.Memory}}' "$(docker compose ps -q backend)"
docker compose exec -T backend du -sh /data
```

`docker stats` 默认连续刷新，可在登录期间观察后按 Ctrl-C；上面使用 `--no-stream` 为单次采样。真实峰值需要有时间戳的连续采样 / 监控。只使用限定字段的 inspect，不输出完整容器环境。

最终资源报告应记录镜像摘要、主机 CPU/RAM、内核、Docker 版本、内存限制、场景、采样区间及峰值；CPU 用百分比或核数时标明单位。不用“健康检查通过”推断真实学校流程、不用容量上限推断人数。
