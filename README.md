# 删库跑路快捷助手

Linux 服务器运维管理面板。当前包含安全底座、系统监控、文件管理和 Nginx 站点。

后端是 FastAPI，前端是 Vue 3 + Vite + Tailwind CSS + Pinia。开发时两边分开起，浏览器只访问前端，接口和 WebSocket 由 Vite 代理到本机后端。

## 目录

```text
backend/
  app/
    auth/            登录、JWT、管理员账号
    commands/        白名单系统命令封装（shell=False）
    monitor/         psutil 采样与查询接口
    files/           文件管理与路径监狱
    nginx/           站点配置、语法检查与证书申请
    websocket/       监控数据推送
    middleware/      安全响应头
  tests/             命令执行器与路径越界测试
frontend/
  src/
    api/             Axios。刷新令牌走独立实例
    router/          路由和登录守卫
    stores/          Pinia：会话与监控
    views/           登录页、仪表盘、文件管理、网站
```

## 本地启动

需要 Python 3.9+ 和 Node.js 18+。macOS 可以用来把开发环境跑起来，采集同样走 psutil。两个终端都从仓库根目录开始。

后端：

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

第一次启动会在控制台打印初始管理员账号，用户名是 `admin`，密码随机生成，只打印这一次。请当场记下。

前端：

```bash
cd frontend
npm install
npm run dev
```

浏览器打开 http://127.0.0.1:5173 。

健康检查：

```bash
curl http://127.0.0.1:8000/api/health
```

命令执行器测试（在 `backend/` 下，不必先装依赖）：

```bash
python3 -m unittest tests.test_executor tests.test_file_jail tests.test_nginx
```

## 文件管理

文件接口只允许访问一个根目录。默认是 `backend/data/wwwroot`。Linux 部署时把环境变量 `FILE_ROOT` 设为 `/www/wwwroot`。

`../`、`/etc`、`/root` 以及解析后落到根目录外的路径会被拒绝，并在后端日志里留下越界报警。压缩包里的越界条目同样会拒绝解压。

## 网站

站点配置默认写到 `backend/data/nginx/`。保存前会用这份独立配置执行 `nginx -t`，通过之后才 `nginx -s reload`。检查失败会把配置文件和启用链接恢复回去，并把 Nginx 的错误输出返回给页面。

这个默认模式不会修改 `/etc/nginx`，也不会给系统里正在运行的 Nginx 发信号。要接管宿主机 Nginx 时，再设置：

```bash
NGINX_SYSTEM_MODE=true
NGINX_SITES_AVAILABLE=/etc/nginx/sites-available
NGINX_SITES_ENABLED=/etc/nginx/sites-enabled
```

网站目录只能建在文件根目录里面。点站点列表的「设置」后，左侧可以管理域名、子目录绑定、伪静态、反向代理、SSL、访问限制、流量限制、防盗链、重定向、配置文件和日志。

伪静态写在独立的 `rewrite/域名.conf`，站点配置用 `include` 引入。手写整份配置时，目录不能超出文件根目录，证书和日志也只能落在面板允许的位置。保存时先把候选内容写到临时文件并执行 `nginx -t`，退出码为 0 才覆盖正式配置并重载；失败则正式文件保持原样，并把 Nginx 的错误输出返回给页面。本机没有 nginx 时，保存会被拒绝。

## 安全约定

- 访问令牌 30 分钟，刷新令牌 7 天，刷新时旧令牌立即作废。
- 连续 5 次密码错误会锁定约 5 分钟。
- 系统命令只能通过 `app.commands.executor.run_command` 执行：必须传参数列表，`shell=False`，可执行文件在白名单内。没有网页执行任意命令的接口。
- 服务默认只监听 `127.0.0.1`。
- `backend/data/` 里是 JWT 密钥、密码哈希和刷新令牌，不要提交，也不要拷给别人。

忘记初始密码时，停掉后端，删除 `backend/data/admin.json` 再启动，控制台会重新打印一把新密码。
