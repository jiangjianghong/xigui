# 鉴权与接口访问控制

所有 HTTP 请求先经过统一鉴权。管理员登录会话、有效 API Key、启用的 IP 白名单是三种等价的鉴权方式，任一满足即可访问全部接口和后台页面，包括管理操作、接口文档以及后续新增接口。未通过任何一种鉴权的请求仍被拒绝（公开资源除外）。

## 初始化和登录

升级前先安装依赖，并在服务器初始化管理员密码：

```bash
uv sync
uv run python scripts/set_admin_password.py
```

命令交互输入 6–12 个字符的密码，默认账号 `admin`；可用 `--username 管理员名称` 指定账号。密码不会放在命令行参数或日志里。也可以生成 12 个字符的随机密码并写入一个**不存在的新文件**：

```bash
uv run python scripts/set_admin_password.py --generate-to data/admin-initial-password.txt
```

妥善保存密码后删除该文件。该文件在已忽略的 `data/` 中，不应提交或对外共享。重置密码使用同一个命令，后续请求中原会话立即失效。若配置中已有符合 6–12 字符要求的 `settings.password`，首次启动会迁移成 Argon2id 哈希并移除明文，旧密码继续有效；已有哈希时以哈希为准。旧密码长度超出范围时，请先用上述命令重置。没有密码时启动失败，绝不会退回免密模式。

浏览器打开 `/`，未登录会跳转到 `/ui/login`。`POST /auth/login` 接受 `{"username":"admin","password":"..."}`，请求需带 `X-Requested-With: XMLHttpRequest`。登录成功设置 `xigui_session` Cookie（本项目独立命名，避免覆盖另一个项目的 `xijuan_session`）。Cookie 为 `HttpOnly; SameSite=Strict; Path=/`，`secure_cookie: true` 时还带 `Secure`。

登录地址不带文件后缀，端口沿用 `app.port` 配置；例如端口配置为 `5019` 时是 `http://127.0.0.1:5019/ui/login`。旧的 `/ui/login.html` 和带尾斜杠的 `/ui/login/` 会跳转至 `/ui/login`。登录页、重新登录弹窗和服务端统一校验 6–12 字符，超出范围返回 `422`。

`GET /auth/session` 返回 `authenticated` 和 `authorized`。登录会话还会返回用户名和 `expires_at`；通过 API Key 或白名单时返回对应身份，不返回会话到期时间。`POST /auth/logout` 只使当前登录会话失效并清除 Cookie，不撤销 API Key 或白名单权限。会话默认固定 30 分钟，不因操作续期；到期、退出、重启、加载新的管理员账号或密码哈希时失效。同一来源连续失败 5 次锁定 5 分钟，锁定响应为 `429` 并带 `Retry-After`。所有鉴权方式都失效后，后台会弹出重新登录窗口，保留当前页面的表单；重新登录后需再次提交，不会自动重放修改请求。

## 权限

| 请求 | 管理员会话 | API Key / 白名单 |
| --- | --- | --- |
| `POST /compare` | 允许 | 允许 |
| `GET /admin/rules`、`GET /admin/rules/{rule_id}` | 允许 | 允许 |
| `GET /admin/projects`、`GET /admin/projects/{key}` | 允许 | 允许 |
| `GET /admin/history`、`GET /admin/history/{record_id}` | 允许 | 允许 |
| 新增、修改、删除规则或项目，批量归属调整 | 允许 | 允许 |
| 清空历史、导入、导出、重载配置 | 允许 | 允许 |
| 后台、`/docs`、`/redoc`、`/openapi.json` | 允许 | 允许 |
| 管理 Key / 白名单 | 允许 | 允许 |
| 后续新增接口 | 允许 | 允许 |

`GET /health` 公开返回服务状态、名称和版本，通过任一种鉴权后都能看到额外的规则数量和 ID。登录页及其专用 CSS/JS、会话查询为公开资源。其余静态资源和接口均受保护。

所有 Key 和白名单具有完整管理权限，**没有按项目、规则、调用方隔离数据**。规则查询会包含提示词等规则定义；历史查询会包含所有调用方的输入和结果。仅向可信程序发放凭据。

## API Key 和白名单

后台右上角「接口访问控制」提供：

- API Key：创建（永久或指定未来时间）、启用、禁用、重新生成、删除。重新生成立即作废旧 Key，保留原有效期和启用状态。完整密钥只在创建或重新生成时展示一次。
- 白名单：添加 IPv4/IPv6 单地址或 CIDR、启用、禁用、删除。网段会规范化，单地址等价于 `/32` 或 `/128`，不区分端口。

服务端只保存 Key 的 SHA-256 哈希、名称、末尾标识、创建时间、到期时间及启用状态。列表不返回完整密钥或哈希。

程序示例：

```bash
curl http://localhost:6549/compare \
  -H "Authorization: Bearer $XIGUI_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"rule_id":"R001","field_a":{"value":"2024-001"},"field_b":{"value":"2024-001"}}'
```

全部接口的放行条件是管理员会话 **或** 有效 Key **或** 启用的白名单。因此禁用/删除 Key 后，若来源仍在白名单，访问依然允许。需要逐个停用程序时，不要给它们共享的 NAT 出口添加免密白名单。禁用凭据不会撤销已接收的比对任务。

管理 API：`GET /admin/access-control` 返回 `{version, api_keys, ip_allowlist}`。修改用 `POST /admin/access-control/{kind}`，`kind` 为 `api_keys` 或 `ip_allowlist`：

```json
{
  "version": "列表响应的版本",
  "operation": "create",
  "values": {"name": "财务系统", "expires_at": null}
}
```

`operation` 为 `create/update/rotate/delete`；非创建时需携带 `id`，`rotate` 仅支持 Key。白名单创建需要 `name`、`network`；`update` 支持名称和 `enabled`，Key 可更新有效期、白名单可更新网段。时间必须带时区。修改前校验完整配置，在同目录写临时文件并原子替换；整个文件的版本不一致时返回 `409`，前端刷新列表、保留输入供核对重试。

## 配置与部署

配置仍在 `config/config.yaml`，支持 `CONFIG_PATH` 覆盖。完整示例见 [config.example.yaml](../config/config.example.yaml)。

```yaml
settings:
  username: admin
  password_hash: '<Argon2id 哈希>'
  session_minutes: 30
  secure_cookie: false
access_control:
  api_keys: []
  ip_allowlist: []
  trusted_proxies: []
```

管理员账号、会话时长、Secure Cookie 和可信代理通过 YAML 配置。本项目原本按文件变化自动重载，继续保留：有效的手工修改会在后续请求生效；新密码应由初始化命令产生哈希，启动时才迁移旧明文密码。非法配置会拒绝请求，不回退到旧凭据。

保持单 worker：会话和失败计数保存在进程内存。Python 入口和 Docker 默认均关闭 Uvicorn 代理地址改写。手动启动必须使用：

```bash
uv run uvicorn app.main:app --host 0.0.0.0 --port 6549 --workers 1 --no-proxy-headers
```

直连时忽略客户端提交的 `X-Forwarded-For`。仅当网络直连对端属于 `trusted_proxies`，才从 XFF 最右侧向外解析，并停在第一个不可信节点。无效链拒绝用作白名单匹配。Nginx 等代理应保留真实 `Host`，设置 `X-Forwarded-For` 和 `X-Forwarded-Proto`，只将实际代理的地址/网段列为可信。不要信任 `0.0.0.0/0` 等任意来源。

HTTPS 部署将 `secure_cookie` 设为 `true`。管理员 Cookie 请求仍执行同源校验，登录及修改请求还需要 `X-Requested-With: XMLHttpRequest`，此请求头不是凭据，后台会自动附加；脚本模拟管理员 Cookie 或用接口文档执行修改时也要携带。有效 API Key 和 IP 白名单是独立的高权限凭据，可直接访问全部接口，不受浏览器同源校验和 Cookie CSRF 请求头限制。

YAML 需要对服务进程可写，且其**父目录**也需可写；Docker 挂载整个 `config/` 目录以支持原子替换，避免只挂载单个 YAML 文件。写入使用独立的 `config.yaml.lock` 文件协调服务与密码重置命令，避免并发保存恢复旧凭据；运行期间不要删除锁文件。编辑器手工保存不会遵循该锁，应避开后台保存操作。多个服务实例仍不受支持，因为会话和失败计数并未共享。

## 验证

```bash
uv run pytest tests
```

测试使用独立临时 YAML、内存 SQLite 和模拟 LLM，不读取或修改部署凭据，不连接真实模型服务。鉴权用例覆盖权限矩阵、过期、轮换、白名单替代关系、代理链、CSRF 和配置冲突。部署仍需验证实际 HTTPS、代理链和调用程序传递的请求头。
