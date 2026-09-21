# AGENTS.md — shadowrocket-config

小火箭（Shadowrocket）配置仓库 `leon4z/shadowrocket-config` 的本地工作副本。背景、设计取舍、与基线配置的逐条差异、自测办法都在 `README.md`，这里只写协作约束。

## 硬约束

1. **不要把任何节点信息写进这个仓库。** 仓库是公开的，且 `[Proxy]` 段必须保持为空——节点由小火箭 App 内的订阅管理。不要写入服务器地址、端口、密码、UUID、私钥、订阅 URL，也不要把节点名当分组员写死（分组用 `policy-regex-filter`）。
2. **只改 `src/Shadowrocket.conf`。** `dist/` 是构建产物（已 gitignore），不要手工编辑，也不要手工 commit 到 `release` 分支——那是 CI 的位置。
3. **提交前跑一遍** `python3 scripts/build.py`。它会联网校验全部远程规则集，并检查 `[Rule]` 引用的策略名是否在 `[Proxy Group]` 里定义过。本地跑通再 commit，避免 CI 失败。
4. **不要为了「顺手修好」而扩大改动范围。** 这份配置是在用的东西，路由变化要有据可查。改了什么、为什么、影响哪些域名，写进 commit message 和 README。

## 分支

- `main`：源文件 + 脚本 + 工作流。日常只推这里。
- `release`：只放一个 `Shadowrocket.conf`，由 Actions 发布，供订阅 URL 使用。不要直接推。

## 权威状态从哪里取

改配置前若要与 App 内实际状态对齐，注意 **`Backup/*.conf` 是过期导出**，权威来源是 iCloud Documents 目录下的解析缓存：

```
~/Library/Mobile Documents/iCloud~com~liguangming~Shadowrocket/Documents/
  <配置名>.conf--<hash>.db      ← sqlite，config 表 (section,name,value,option) 是生效配置
  shadowrocket.sync.plist       ← configs 段列出哪些配置是 alive
```

节点名的权威来源是 Group Container（需要完全磁盘访问权限）：

```
~/Library/Group Containers/group.com.liguangming.Shadowrocket/ServerManager   ← NSKeyedArchiver，$objects 里是节点条目
```

## 已知未验证项

见 README「已知取舍与未验证项」。核心两条：远程规则集文件内部的 `no-resolve` 是否被逐条 honor、`DOMAIN-SET` 的子域匹配语义——都没有实机验证，只依据上游文档与配置注释。改动这两块相关的东西时不要当成已确认事实。
