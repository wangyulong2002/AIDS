# 开发环境部署说明（DEP-01 / DEP-02）

## 快速开始

```bash
cd deploy
cp .env.example .env          # 按需修改端口与密码
docker compose up -d          # 启动默认档（不含 search 档的 Milvus）
```

首次启动会自动初始化数据库（执行 `docs/sql/` 下三个脚本并灌入种子数据）。
看到日志 `✅ 数据库初始化全部完成` 即成功。

### 分档启动

按需选择，避免一次性吃掉全部内存：

| 档位 | 命令 | 服务 | 内存需求 |
|------|------|------|----------|
| 最小 | `docker compose up -d mysql redis nginx` | MySQL + Redis + Nginx | ≥ 2GB |
| 默认 | `docker compose up -d` | 上面 + Kafka + MinIO | ≥ 3.5GB |
| 全量 | `docker compose --profile search up -d` | 上面 + Milvus（向量库，供 AI RAG） | ≥ 6GB |

> 上表为**宿主可用内存下限**。全量档 6GB 是下限（偏紧），**8GB 为舒适值**。
> 本口径为全仓唯一权威：PRD §13 / §15 与 `docker-compose.yml` 头部注释均引用本表。
> 上表「内存需求」指**宿主可用内存下限**（含 Docker Desktop / WSL2 自身开销），高于容器净占用。
> **v1.3 本机实测（空载，无应用连接）**：默认档 5 个常驻合计 **≈0.9GB**；再加 `search` 档 3 个（全量 8 常驻）合计 **≈1.5GB**。
> **v1.2**：商品搜索已改用 MySQL 全文索引（PRD §5.6），`search` profile 下不再有 Elasticsearch，
> 只剩 Milvus 三件套（`milvus-etcd` + `milvus-minio` + `milvus`）。
> **v1.3**：后端全栈切换 FastAPI、**移除 Nacos**（静态配置走 `.env`/pydantic-settings，动态配置走 `sys_config` 表 + Redis 热更新），
> 编排由 10 个服务降为 **9 个**，默认档内存需求随之下降；踩坑清单中原 Nacos `NACOS_AUTH_TOKEN` 一条已不再适用并移除。

### 服务入口

| 服务 | 地址 | 账号 |
|------|------|------|
| Nginx（API 统一入口） | http://localhost（改过 `HTTP_PORT` 则为对应端口） | — |
| MinIO 控制台 | http://localhost:9001 | 见 `.env` |
| Milvus 健康检查 | http://localhost:9091/healthz | — |
| Kafka（宿主机访问） | localhost:29092 | — |

### 数据库账号（来自 seed.sql）

| 用途 | 账号 | 密码 |
|------|------|------|
| MySQL root | root | 见 `.env` |
| 后台管理 | `admin` | `Admin@123456` |
| 客服坐席 | `agent01` | `Agent@123456` |

---

## 环境适配记录（实测）

### 1. WSL2 下 bind mount 为空 —— 已改用构建期 COPY 规避

**现象（历史）**：Docker Desktop for WSL2 下，bind mount 进来的目录在容器内为空（含 `/tmp`），
MySQL 初始化脚本读不到 `docs/sql`，启动报
`accessing specified distro mount service ... ubuntu.sock: no such file or directory`。

**当前方案**：运行期不再依赖任何 bind mount，改为**构建期把配置与 SQL 打进自建镜像**：

| 服务 | 自建镜像 | 构建期 COPY 的内容 |
|------|----------|--------------------|
| mysql | `aids/mysql-init:8.4` | `docs/sql/` + `init/00-init-databases.sh` |
| nginx | `aids/nginx:1.27` | `conf.d/default.conf` + `html/` |

这样在 Docker Desktop / 原生 Docker / macOS 上行为一致。构建上下文指向**项目根目录**
（`context: ..`），因为 SQL 在 `docs/sql/`，不在 `deploy/` 下。

### 2. 宿主 80 / 3306 被占用，Docker Desktop 无法发布端口

**现象**：`docker compose ps` 里 mysql 只有 `3306/tcp`、nginx 只有 `80/tcp`，**没有**
`0.0.0.0:3306->3306` 这类映射；`docker run -p 3306:...` 报
`Bind for 0.0.0.0:3306 failed: port is already allocated`，但 `docker ps` 中并无其它容器占用——
是 **Windows 宿主机进程**（如已安装的 MySQL / IIS / http.sys 保留段）占用。

**处理**：在 `deploy/.env` 里改宿主端口（该文件已被 `.gitignore` 排除，属机器本地配置）：

```
HTTP_PORT=18080      # 不要选 8080/8000/8081：那是后端 / AI / Mock 支付的应用端口
MYSQL_PORT=13306
DB_PORT=13306        # 应用连接串必须同步修改
```

改完执行 `docker compose up -d` 重建即可。
**注意**：`DB_PORT` 必须跟着改，否则宿主机上的后端会连到自己 3306 的 MySQL（Windows 那个），
而不是容器里的库。

---

## 目录结构

```
deploy/
├── docker-compose.yml          中间件编排（profiles 分档）
├── .env.example                环境变量模板（含所有密钥占位）
├── nginx/
│   ├── Dockerfile              Nginx 自建镜像（构建期 COPY 配置）
│   ├── conf.d/default.conf     反代规则（SSE 关闭 buffering 是关键）
│   └── html/index.html         占位页（前端工程启动后由 Vite 接管）
├── mysql/
│   ├── Dockerfile              MySQL 自建镜像（构建期 COPY SQL，规避 bind mount）
│   └── init/
│       └── 00-init-databases.sh 数据库初始化（显式控制执行顺序）
└── app/
    ├── backend.Dockerfile      FastAPI 主业务
    ├── ai.Dockerfile           FastAPI
    ├── frontend.Dockerfile     前端（商城/后台共用，参数区分）
    └── mock.Dockerfile         Mock 渠道服务（FastAPI）
```

---

## 几个容易踩的坑

**1. MySQL 初始化脚本的执行顺序**

`docker-entrypoint-initdb.d` 按**文件名字典序**执行。而 `docs/sql/` 下的文件名排序是
`mock_schema.sql` → `seed.sql` → `schema.sql`，**seed 会先于建表执行而失败**。

因此没有直接挂载 `docs/sql`，而是由 `00-init-databases.sh` 显式按
`schema → mock_schema → seed` 的顺序执行。

**2. Nginx 反代到宿主机需要 `extra_hosts`**

`host.docker.internal` 在 Linux/WSL2 下默认不可解析。compose 中已配置：

```yaml
extra_hosts:
  - "host.docker.internal:host-gateway"
```

**3. SSE 必须关闭 `proxy_buffering`**

AI 客服走 SSE 流式输出。若开启 buffering，Nginx 会缓冲整个响应，
首字延迟从 1s 劣化到 10s+。`nginx/conf.d/default.conf` 中已针对 `/api/ai/` 关闭。

**4. Redis 不能淘汰预扣库存的 key**

`maxmemory-policy` 设为 `noeviction`。若用 `allkeys-lru`，
高并发下预扣的库存 key 可能被淘汰，导致超卖。

**5. 商品搜索不走 ES（v1.2 变更）**

早期方案是 Elasticsearch + IK 分词器，**v1.2 已撤销**：商品搜索改用 MySQL 8 全文索引
（`FULLTEXT ... WITH PARSER ngram`，见 PRD §5.6），同库强一致、免同步链路，并省掉一个中间件。
`deploy/elasticsearch/` 目录（含自建 ES+IK 镜像）已删除。

撤销的直接触发原因是本机拉不下 ES 镜像：`docker.elastic.co` 的镜像层存放在 **Cloudflare R2**
（`*.r2.cloudflarestorage.com`），本机 DNS **只返回 IPv6、不给 A 记录**（典型污染），
而机器没有 IPv6 连通性 → 大层下载长时间停滞。记录于此，供将来真需要引入搜索引擎时参考。

**6. 重新初始化数据库（只 `down -v` 不生效）**

initdb 脚本只在 **数据卷为空时**执行一次。但 `docs/sql/` 是 **构建期 COPY 进 `aids/mysql-init` 镜像**的，
所以光删卷不够 —— 卷重建后跑的仍是镜像里的旧 SQL，表数会「莫名其妙没变」。必须先重建镜像：

```bash
# 只重置主库（推荐，不动其它服务的卷）
docker compose build mysql
docker compose rm -sf mysql
docker volume rm aids_mysql-data
docker compose up -d mysql
docker compose logs mysql | grep -A6 '初始化结果自检'   # 应见 aids_shop = 38

# 全部推倒重来（⚠️ 会删掉 Kafka/MinIO/Milvus 等所有数据卷）
docker compose build mysql nginx
docker compose down -v
docker compose up -d
```

**7. MinIO 镜像走 `quay.io`，不要用 Docker Hub**

MinIO 官方已把镜像迁至 `quay.io/minio/minio`；Docker Hub 的旧日期标签被下架，
经国内加速器访问会返回 `error from registry: denied` / `403`。
compose 中已统一为 `quay.io/minio/minio` 与 `quay.io/minio/mc`。

**8. Nginx 配置的两个上下文陷阱**

- `log_format` 只能在 **`http` 上下文**声明。写进 `server{}` 会让 `nginx -t` 报
  `"log_format" directive is not allowed here`。本仓库把它放在 `conf.d/default.conf` 顶层
  （该目录已被 `http{}` 包含）。
- `proxy_pass http://minio:9000` 会在 **nginx 启动时**解析上游域名：构建期 `nginx -t`
  没有该 DNS，且 MinIO 重启换 IP 后还需 reload。故 `/files/` 改用变量 + Docker 内置 DNS
  （`resolver 127.0.0.11`）把解析推迟到运行期。

**9. 改完这些文件必须重建镜像（构建期 COPY，无运行期挂载）**

为规避 WSL2 bind mount 故障（见踩坑 #6 与「运行基线」），`mysql` / `nginx` 自建镜像把内容
COPY 进了镜像层。运行期**没有任何挂载**，所以改源文件不 rebuild 就等于没改：

| 改了什么 | 必须重建 | 否则的现象 |
|----------|----------|------------|
| `docs/sql/*.sql`、`mysql/init/*.sh` | `docker compose build mysql` | 建表/种子仍是旧的（配合踩坑 #6） |
| `nginx/conf.d/*`、`nginx/html/*` | `docker compose build nginx` | 反代规则与占位页内容仍是旧的 |

改完记得 `docker compose up -d --force-recreate <svc>` 让新镜像真正换上。

**10. Milvus：REST 端口在 19530，且集合必须显式 load**

v1.3 起 `search` 档冒烟实测到的两件事（都会坑掉 AI 知识库联调）：

- **RESTful v2 与 gRPC 同端口 19530**，不是 9091。9091 只服务 `/healthz` 与 `/metrics`，
  对它打 `/v2/vectordb/*` 会返回 `404 page not found`（这是 Milvus 自己的 404 页，容易误判成服务没起）。
  健康检查用 9091、数据面操作用 19530：
  ```bash
  curl -s http://localhost:9091/healthz                 # 存活探针
  curl -s -X POST -H 'Content-Type: application/json' -d '{}' \
       http://localhost:19530/v2/vectordb/collections/list
  ```
- **未 load 的集合做向量检索会静默返回空**：`insert` 与标量 `query` 都正常，唯独 `search` 返回
  `"data":[]` 且 `code:0` 不报错。`dimension` 形式的 quick-create **不会自动 load**，需显式调用：
  ```bash
  curl -s -X POST -H 'Content-Type: application/json' -d '{"collectionName":"<col>"}' \
       http://localhost:19530/v2/vectordb/collections/load
  ```
  对 AI 侧的连带风险见 PRD §9.2「检索前置」——重建时漏了 load 就切别名，会造成静默的检索空窗。

**11. Python 镜像在本机构建不完：`apt-get` 拉工具链极慢（v1.3 实测）**

- **现象**：`docker build` backend / ai / mock 三个镜像，全部卡在 builder 阶段第一层
  `apt-get install build-essential gcc`——跑到 **1000 秒以上仍在从 `deb.debian.org` 下载**
  （单个 `cpp-14` 包 11MB），本机链路对该站点明显受限，最终未在合理时间内产出镜像。
- **不是 Dockerfile 的缺陷**：v1.3 **未改动**的 `ai.Dockerfile` 在同一步同样失败，
  说明这是环境网络约束，与 backend / mock 改写为 FastAPI 无关。三者共用这一段模板。
- **因此当前验证边界要讲清楚**：三个应用镜像只做到**静态核对**（两阶段结构、`USER`、
  `HEALTHCHECK` 路径与端口、`gunicorn app.main:app` 模块路径一致），
  **端到端构建 + `/health` 探活尚未在本机跑通**，随 M1 有真实源码后在 CI 首验。
- **三条出路（M1 前择一，属供应链/镜像源决策，未擅自动 Dockerfile）**：
  1. builder 阶段换国内 apt 源（`debian.sources` 指向 `mirrors.tuna.tsinghua.edu.cn` 等）+ pip 换 index；
  2. 若依赖全部有 manylinux wheel，可去掉 `build-essential gcc` 这一层（引入 `cryptography`/`bcrypt` 后需再评估，二者有 wheel 但装源码包时仍需编译器）；
  3. 换到外网稳定的机器或 CI runner 上构建。
