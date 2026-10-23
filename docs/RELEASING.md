# 发行维护与向上游贡献

## 本仓库的发行方式

发行仓库为 `Mr-Pian/GoodnightUESTC-Web`，开发分支为 `web`。v1.1.0 提供 Linux amd64 镜像、完整源码部署包、逐条命令的 `DEPLOYMENT.md` 和 `SHA256SUMS`。Docker 只运行应用，域名、证书与公网代理由部署者接入。面向运维的安装步骤见 [部署指南](../deploy/QUICKSTART.md)，资源数字为 [规划预算](../deploy/RESOURCES.md)，尚不是性能基准。

维护新版本时：

1. 修改 `VERSION`、`CHANGELOG.md`，创建 `docs/RELEASE_vX.Y.Z.md`，同步部署示例中的固定版本与镜像标签。真实 `.env`、主机地址、Key、Cookie、数据卷和原始部署报告不能提交。
2. 在开发环境运行合成测试，并在 GitHub Actions 检查 `Test Docker backend`。容器验证含严格 umask、非 root、只读运行、Chromium、合成浏览器流程和代理契约；它们不代表真实学校提交或全部浏览器实测。
3. 推送到自己的 `web` 分支后，在 Actions → `Publish release and image` → Run workflow，选择 `web`，版本输入留空即可读取所选提交的 `VERSION`；也可明确输入匹配的 `vX.Y.Z`。构建前会检查版本格式、标签一致性和对应发行说明。也可推送同名版本 tag 触发；二选一，避免重复发行。
4. 流水线先验证，再构建并推送 GHCR 镜像，最后生成源码附件与 Release；手动触发时由 Release 步骤在相同提交创建版本 tag。失败时先查看实际失败阶段；若镜像已推送但 Release 未完成，先核对摘要，避免把不同内容覆写为同一版本。已经正式发行的版本应保留，修复使用新版本号。
5. 首次创建 GHCR 包后，到包设置确认访问级别为 **Public**，并检查关联本仓库。公开仓库不保证首次创建的包自动公开。验证未登录客户端可拉取固定标签，核对 Release 中的 digest，并下载附件执行 `sha256sum -c SHA256SUMS`。包仍私有时不宣称镜像安装可用。

提交标题或 Release 标题不会自动修改 `VERSION`。出现版本不匹配时，核对日志里的 `VERSION`、`requested tag` 和 `expected tag`，同步版本文件与发行说明并提交、推送，然后从 `web` **新建一次 Run workflow**。旧运行的 **Re-run jobs** 仍使用原提交和原输入，无法带上后来提交的修复；仅在原提交无需修改、故障为临时环境问题时重跑原任务。不要为了绕过检查填入已经发行的旧版本，也不要预先手动创建同名 Release，工作流最后会创建它。

已配置 GitHub CLI 的维护者也可手动触发（凭据由维护者在本机管理，不填入仓库）：

```sh
gh workflow run release.yml --repo Mr-Pian/GoodnightUESTC-Web --ref web -f version=v1.1.0
gh run list --repo Mr-Pian/GoodnightUESTC-Web --workflow release.yml --limit 5
```

Linux amd64 空白测试环境的匿名拉取（使用临时 Docker 客户端配置，保留原凭据）：

```sh
PUBLIC_PULL_CONFIG=$(mktemp -d)
printf '{"auths":{}}\n' > "$PUBLIC_PULL_CONFIG/config.json"
docker --config "$PUBLIC_PULL_CONFIG" pull ghcr.io/mr-pian/goodnightuestc-web:1.1.0
rm "$PUBLIC_PULL_CONFIG/config.json"
rmdir "$PUBLIC_PULL_CONFIG"
```

版本 tag 用于固定源码；部署建议固定版本镜像或 digest，`latest` 仅供发现最新版本。发布附件用 `git archive` 从跟踪源码生成；Docker 构建上下文另有白名单，不包含测试、部署配置和本地数据。不要上传未经检查的工作目录压缩包。

## 把 Web 改造合入原作者的新分支

本仓库在 GitHub 显示为 `LCX-luo/GoodnightUESTC` 的 fork，可直接做跨仓库 PR。**你只能在自己的仓库创建分支；原仓库的新目标分支需要原作者或有写权限的维护者先创建。PR 本身不会创建上游目标分支。** 本次提供步骤和 PR 正文草稿，不自动联系作者或创建 PR。

推荐流程：

1. 与原作者约定：在 `LCX-luo/GoodnightUESTC` 创建专用 `web` 分支，起点由原作者选择；如果使用其他分支名，后续 base 一并替换。
2. 目标分支存在后，进入上游 Pull requests → New pull request → compare across forks。base repository 选 `LCX-luo/GoodnightUESTC`，base 选 `web`；head repository 选 `Mr-Pian/GoodnightUESTC-Web`，compare 选 `web`。
3. 核对 Files changed，尤其是删除 Windows / APK 和原构建工作流的部分。这个完整 Web 改造应进入独立分支；原作者仍维护桌面 / Android 时，需明确审阅这些删除。通用修复可另行提取。
4. 使用 [PR 正文草稿](UPSTREAM_PR.md)，写明 v1.1.0 Release 链接与该次 CI 结果。由原作者审阅并 merge 后，上游才得到此 Web 分支内容。fork 的 Release、GHCR 包、包权限和 Actions 设置不会随着代码合并自动迁移，原作者需在上游单独配置发行。

在浏览器中可从 [上游 PR 页面](https://github.com/LCX-luo/GoodnightUESTC/pulls)开始操作。没有上游 `web` 时，不把 base 留在 `main` 直接提交。需要先确认本地 remote 时，只读检查：

```sh
git remote -v
git branch -vv
git fetch upstream
git ls-remote --heads upstream refs/heads/web
```

最后一条应返回 `refs/heads/web`。如果为空，联系原作者建立分支；不要向上游直接 push，也不需要删除本地分支、重写历史或使用 `--allow-unrelated-histories`。

已经配置 GitHub CLI 且确认上游分支存在时，可由提交者执行：

```sh
gh pr create --repo LCX-luo/GoodnightUESTC \
  --base web --head Mr-Pian:web \
  --title '新增独立 Docker / Web 部署分支' \
  --body-file docs/UPSTREAM_PR.md
```

如果原作者希望全新独立发行历史，应先由其确定迁移方案；不要为减少提交数强行覆盖已有上游分支。上游同步继续通过审阅后的 PR 或提取通用提交进行。
