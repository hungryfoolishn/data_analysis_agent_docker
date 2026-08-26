# Docker 部署指南

> 将 DeepAnalyze 打包为 Docker 镜像运行，基于 `telebi-telebi-base:latest`（含 conda 25.1.1）。

## 镜像设计

| 项 | 值 |
|---|---|
| 基础镜像 | `telebi-telebi-base:latest` |
| 运行用户 | `telebi`（uid 999） |
| Conda 环境 | `smolagents`（python=3.10，与本地对齐） |
| 暴露端口 | `8888`（FastAPI）/ `8501`（Streamlit） |
| 持久化目录 | `workspace/`、`temp_uploads/`（volume 挂载到宿主机） |

镜像内已预装中文字体（`fonts-noto-cjk` + `fonts-wqy-*`），匹配 `fix_chinese()` 的 fallback 列表。

## 构建与启动

```bash
# 构建镜像并启动（首次）
docker compose up -d --build

# 查看状态
docker compose ps

# 查看日志
docker compose logs -f
```

## 改源码后重新部署（推荐方案）

源码通过 `COPY . /app/` 打进镜像，**不是**挂载。改源码后，宿主机改动不会自动同步到容器。

正确做法是重新构建镜像：

```bash
docker compose up -d --build
```

- pip 依赖层有缓存，只重新跑 `COPY . /app/` 这一步，约 2-5 秒
- 仅当 `requirements.txt` 或 `requirements-langgraph.txt` 变化时，才会触发 pip 重新安装（耗时较长）

> ⚠️ 以下操作**不会**让源码改动生效：
> - `docker compose restart` —— 容器仍使用旧镜像
> - `docker exec` 进容器重启进程 —— 容器内是旧代码副本
> - `docker compose up -d`（不带 `--build`）—— Compose 检测到镜像已存在就直接复用

## 端口映射

`docker-compose.yml` 中默认映射到宿主机高位端口，避开宿主机已占用的 8888/8501：

```yaml
ports:
  - "18888:8888"   # FastAPI backend
  - "18501:8501"   # Streamlit frontend
```

如需修改，编辑 `docker-compose.yml` 后 `docker compose up -d` 即可。

## 外网访问

`.env` 中的 `FILE_SERVER_BASE` 用于生成给浏览器点击的文件下载链接，必须设为外网可达的地址：

```bash
FILE_SERVER_BASE=http://<外网IP>:18888
```

`API_BASE_URL` 保持默认 `http://localhost:8888` 即可（streamlit 与 backend 同容器，走容器内回环）。

## 配置项

所有配置通过 `.env` 文件注入，`docker-compose.yml` 在运行时读取：

| 变量 | 默认值 | 说明 |
|---|---|---|
| `DEEPSEEK_API_KEY` | 必填 | DeepSeek API 密钥 |
| `DEEPSEEK_MODEL_ID` | `deepseek-chat` | 模型 ID |
| `DEEPSEEK_API_BASE` | `https://api.deepseek.com/v1` | API 基地址 |
| `MAX_CONCURRENT_AGENTS` | `3` | 最大并发 agent 数 |
| `SESSION_TTL_HOURS` | `24` | 会话 TTL |
| `FILE_SERVER_BASE` | `http://localhost:8888` | 文件下载外网地址 |
| `START_MODE` | `both` | 启动模式：`both` / `backend` / `frontend` |

## 常用命令

```bash
docker compose up -d --build   # 构建并启动（改代码后用）
docker compose up -d           # 启动（不重建）
docker compose ps              # 查看状态
docker compose logs -f         # 跟踪日志
docker compose restart         # 重启（不重建，代码改动不生效）
docker compose down            # 停止并移除容器
docker exec -it deepanalyze bash   # 进容器调试
```

## 健康检查

```bash
# Backend
curl http://localhost:18888/health

# Streamlit
curl -I http://localhost:18501/
```

## 故障排查

### 容器显示 `(unhealthy)` 但服务正常

`docker ps` 中容器状态为 `(unhealthy)`,但后端 `/health`、前端首页都能正常访问。

**根因**：基础镜像 `telebi-telebi-base:latest` 自带一条 `HEALTHCHECK`,探测的是 `http://0.0.0.0:8001/health`(telebi 自有服务端口)。本镜像并未运行 8001 服务,所以该检查永远失败,容器被误判为 unhealthy——实际后端(8888)、前端(8501)都是健康的。危害是误导运维、在 Swarm/k8s 等编排器里可能触发误重启。

**解决（已内置）**：`Dockerfile` 已用一条指向真实后端的 `HEALTHCHECK` 覆盖了继承的那条:

```dockerfile
HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD curl -fsS http://localhost:8888/health || exit 1
```

启动后约 20~50s 内状态会转为 `healthy`。确认当前健康状态:

```bash
docker inspect --format '{{.State.Health.Status}}' deepanalyze
# 期望: healthy
```

若仍为 unhealthy,先确认用的是新镜像(HEALTHCHECK 改动需 `docker compose up -d --build` 重建生效),再查健康检查日志:

```bash
docker inspect --format '{{range .State.Health.Log}}exit={{.ExitCode}} out={{.Output}}{{end}}' deepanalyze
```

### 端口冲突
```bash
# 查看占用端口的进程/容器
ss -tlnp | grep -E ':(18888|18501)'
docker ps --format 'table {{.Names}}\t{{.Ports}}' | grep -E '18888|18501'
```

### matplotlib 权限告警
镜像已设置 `MPLCONFIGDIR=/tmp/matplotlib-cache`，正常情况下不会出现。若仍出现，检查容器内 `telebi` 用户对该目录的写权限。

### `temp_uploads` / `workspace` 上传报 PermissionError
前端上传文件时报 `PermissionError: [Errno 13] Permission denied: 'temp_uploads/xxx'`。

**根因**：`workspace/` 和 `temp_uploads/` 通过 bind mount 挂进容器，运行时用的是**宿主机目录的属主**（通常是 root），覆盖了镜像构建时 `chown telebi:telebi /app` 的结果。容器以非 root 的 `telebi`(uid 999) 运行，写不进 root 属主的目录。

**机制（已自愈）**：Dockerfile 里**故意不设** `USER telebi`，entrypoint 以 root 启动 → `chown -R telebi:telebi /app/workspace /app/temp_uploads` 修正挂载目录属主 → `exec gosu telebi` 降权后再启动 backend/frontend。所以无论宿主机目录属主是谁，容器起来都能自愈，应用始终以 `telebi` 运行。

**仍报错的排查**：
```bash
# 1) 确认容器内属主被改回 telebi（应为 telebi:telebi）
docker exec deepanalyze stat -c '%U:%G %n' /app/temp_uploads /app/workspace
# 2) 确认 gosu 已装进镜像
docker exec deepanalyze command -v gosu
# 3) 确认 backend 以 telebi 运行
docker exec deepanalyze ps -o user=,comm= -C python
```
若属主仍是 root，说明用的是旧镜像（entrypoint 改动需 `docker compose up -d --build` 重建生效）。

### 字体不显示中文
确认镜像内已安装中文字体：
```bash
docker exec deepanalyze fc-list :lang=zh | head
```

### `conda activate smolagents` 失败

进容器后执行 `conda activate smolagents` 报错：
```
CondaError: Run 'conda init' before 'conda activate'
```

如果再执行 `conda init` 又会因容器内无 `sudo` 而失败。

**原因**：`conda activate` 需要 shell 先 source conda 的 hook 脚本，而 `conda init` 是给交互式 shell 永久配置用的（要改 `/etc/skel` 等，需要 sudo）。容器场景下不需要走这条路。

**解决方案**：镜像构建时已通过 `ENV PATH=/opt/conda/envs/smolagents/bin:$PATH` 把 smolagents 环境排在 PATH 第一位，`python`/`pip` 默认就是 smolagents 环境的，**无需 activate**：

```bash
$ docker exec -it deepanalyze bash
telebi@xxx:/app$ which python
/opt/conda/envs/smolagents/bin/python
telebi@xxx:/app$ python --version
Python 3.10.20
```

如果确实需要 activate（比如希望提示符显示 `(smolagents)`），手动 source 一下 hook 再 activate：

```bash
source /opt/conda/etc/profile.d/conda.sh
conda activate smolagents
```

三种用法对比：

| 场景 | 命令 | 说明 |
|---|---|---|
| 直接用（推荐） | `python xxx.py` | PATH 已配好，默认就是 smolagents 环境 |
| 临时 activate | `source /opt/conda/etc/profile.d/conda.sh && conda activate smolagents` | 让提示符显示环境名 |
| 永久 activate | 在 `~/.bashrc` 加上面两行 | 进容器自动 activate（需重新构建） |

## 文件清单

| 文件 | 作用 |
|---|---|
| `Dockerfile` | 镜像构建定义 |
| `.dockerignore` | 构建上下文排除规则（含 `.env`、`workspace/` 等） |
| `docker-entrypoint.sh` | 容器启动脚本，拉起 backend + frontend |
| `docker-compose.yml` | 编排定义，含端口映射、环境变量、volume 挂载 |
| `.env` | 运行时配置（不入镜像） |
