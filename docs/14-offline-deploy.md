# 内网离线部署指南

> 适用场景：内网机器无法访问外网，需要在外网机器构建好镜像后传输到内网运行。

## 整体流程

```
外网机器                          内网机器
─────────                         ─────────
1. 构建镜像
   docker compose up -d --build
2. 导出镜像 tar
   docker save ...
3. 传输 tar ──────────────────►  4. 加载镜像
                                   docker load -i ...
                                 5. 配置 .env
                                 6. 启动
                                   docker compose up -d
```

## 一、外网机器操作

### 1. 构建镜像

在项目根目录执行：

```bash
docker compose up -d --build
```

构建完成后验证镜像存在：

```bash
docker images deepanalyze
# REPOSITORY    TAG       IMAGE ID       CREATED          SIZE
# deepanalyze   latest    xxxxxxxxxxxx   xx seconds ago   6.07GB
```

### 2. 导出镜像

需要导出两个镜像：基础镜像 + 应用镜像（内网机器如果没有基础镜像，应用镜像无法启动）。

```bash
# 创建导出目录
mkdir -p deepanalyze-images

# 导出基础镜像（约 5GB）
docker save telebi-telebi-base:latest -o deepanalyze-images/telebi-telebi-base.tar

# 导出应用镜像（约 6GB）
docker save deepanalyze:latest -o deepanalyze-images/deepanalyze.tar

# 校验文件
ls -lh deepanalyze-images/
# -rw------- 1 root root 5.0G  telebi-telebi-base.tar
# -rw------- 1 root root 6.0G  deepanalyze.tar
```

### 3. 同时准备项目配置文件

内网机器启动时需要 `docker-compose.yml` 和 `.env`，一起打包：

```bash
tar -czf deepanalyze-deploy.tar.gz \
    deepanalyze-images/ \
    docker-compose.yml \
    docker-entrypoint.sh \
    .env.example
```

> ⚠️ 不要打包 `.env`（含 API key），让内网机器单独配置。

### 4. 传输到内网

用 scp / U盘 / 内网文件服务器传输 `deepanalyze-deploy.tar.gz`。

## 二、内网机器操作

### 1. 解压部署包

```bash
mkdir -p /opt/deepanalyze
tar -xzf deepanalyze-deploy.tar.gz -C /opt/deepanalyze
cd /opt/deepanalyze
```

### 2. 加载镜像

```bash
# 先加载基础镜像
docker load -i deepanalyze-images/telebi-telebi-base.tar

# 再加载应用镜像
docker load -i deepanalyze-images/deepanalyze.tar

# 验证
docker images | grep -E "telebi-telebi-base|deepanalyze"
```

### 3. 配置环境变量

```bash
cp .env.example .env
vi .env
```

必填项：

```bash
# DeepSeek API 密钥（内网若用代理 LLM 服务，改为对应地址）
DEEPSEEK_API_KEY=sk-xxx
DEEPSEEK_API_BASE=https://api.deepseek.com/v1

# 外网可达地址（用于浏览器下载 workspace 文件）
# 改为内网机器的实际 IP
FILE_SERVER_BASE=http://<内网机器IP>:18888
```

### 4. 启动服务

```bash
docker compose up -d
```

> 内网机器**不需要** `--build`，因为镜像已经从 tar 加载。

### 5. 验证

```bash
# 容器状态
docker compose ps

# Backend 健康检查
curl http://localhost:18888/health

# Streamlit 前端
curl -I http://localhost:18501/
```

预期输出：

```
{"status":"ok","sessions":0,"concurrent_agents":0,"max_concurrent_agents":3}
HTTP/1.1 200 OK
```

## 三、更新镜像

后续代码更新时，重复"外网构建 -> 导出 -> 传输 -> 内网加载"流程：

```bash
# 外网
docker compose up -d --build
docker save deepanalyze:latest -o deepanalyze.tar

# 传输后内网
docker load -i deepanalyze.tar
docker compose up -d
```

基础镜像通常不需要更新，除非 Dockerfile 修改了基础镜像或系统包。

## 四、磁盘空间规划

| 项 | 大小 | 说明 |
|---|---|---|
| `telebi-telebi-base.tar` | ~5GB | 基础镜像 tar |
| `deepanalyze.tar` | ~6GB | 应用镜像 tar |
| 镜像加载后 | ~11GB | docker images 占用 |
| 容器运行时 | <1GB | workspace 数据 |
| **总计** | **~23GB** | 含 tar 文件（可清理） |

启动验证通过后可删除 tar 文件释放空间：

```bash
rm deepanalyze-images/*.tar
```

## 五、常见问题

### 1. docker load 报错 "invalid diffID"

可能 tar 文件传输过程中损坏。用 `md5sum` 校验完整性：

```bash
# 外网
md5sum deepanalyze-images/*.tar > checksums.txt

# 内网
md5sum -c checksums.txt
```

### 2. 内网启动后无法连接 DeepSeek API

内网机器若无法访问 `api.deepseek.com`，需配置内网 LLM 代理：

```bash
# .env
DEEPSEEK_API_BASE=http://内网LLM代理/v1
```

或使用兼容 OpenAI 协议的本地模型服务（如 vLLM、Ollama）。

### 3. 端口冲突

内网机器若已占用 18888/18501，修改 `docker-compose.yml`：

```yaml
ports:
  - "<可用端口>:8888"
  - "<可用端口>:8501"
```

同步修改 `.env` 中的 `FILE_SERVER_BASE`。

### 4. 容器启动失败但日志无报错

```bash
# 查看完整日志
docker compose logs --tail 200

# 进容器调试
docker exec -it deepanalyze bash
# 容器内手动启动 backend 验证
conda activate smolagents
python -m langgraph_langchain.api_server_langgraph
```

## 六、快速参考命令

```bash
# === 外网机器 ===
docker compose up -d --build
docker save telebi-telebi-base:latest -o telebi-telebi-base.tar
docker save deepanalyze:latest -o deepanalyze.tar

# === 内网机器 ===
docker load -i telebi-telebi-base.tar
docker load -i deepanalyze.tar
docker compose up -d
docker compose logs -f
```
