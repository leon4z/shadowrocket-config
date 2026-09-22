# AGENTS.md — shadowrocket-config

小火箭（Shadowrocket）配置仓库 `leon4z/shadowrocket-config` 的本地工作副本。背景、覆盖规格逐项说明、与上游的差异、未验证项、自测办法都在 `README.md`，这里只写协作约束。

## 硬约束

1. **不要往仓库里写节点信息。** 仓库是公开的，`[Proxy]` 段必须保持为空——节点由小火箭 App 内的订阅管理。不要写入服务器地址、端口、密码、UUID、私钥、订阅 URL，也不要把节点名当分组员写死（分组用 `policy-regex-filter`）。
2. **日常分流修改集中在 `src/overrides.py`，采样筛选输入为 `src/selection.json`。** 清单只能包含已校验的节点名正则和汇总元数据，禁止连接信息；生成器能力变化须同步测试。`dist/` 是构建产物（已 gitignore），`release` 分支由 CI 发布，两者都不要手工改或手工推。Karing 那套产物是 `render_karing.py` 从小火箭配置渲染出来的，**不要单独去改 Karing 的 JSON 或规则集**——要改就改规格，两个客户端一起变。
3. **不要在 `src/overrides.py` 里存完整配置行。** 覆盖规格必须用「模式匹配」表达（比如「所有指向 PROXY 的地方换成速度」），不能用行号或整行替换——上游随时会增删行，整行替换会静默失配。上游新增服务分组时应该自动被接住，不需要改规格。
4. **提交前跑一遍** `python3 scripts/build.py`。它会联网校验全部远程规则集、检查锚点、检查策略名解析。本地跑通再 commit。
5. **不要为了「顺手修好」而扩大改动范围。** 这份配置是在用的东西，路由变化要有据可查。改了什么、为什么、影响哪些域名，写进 commit message 和 README。
6. **`fallback` 组不要配 `tolerance`。** 手册里 `tolerance` 是「只有当新优胜者的分数高于旧优胜者加公差时才换线」——「优胜者」是 `url-test` 择优的概念。`fallback` 按可用性切换、不做择优比较，配了是无效参数。只有 `url-test` 组该有 tolerance。

## 分支

- `main`：规格 + 脚本 + 工作流。日常只推这里。
- `release`：通用 `Shadowrocket-*` 与个人 `leon4z-*` 各三份配置，以及 Karing 产物。由 Actions 发布，不要直接推。

## 改动会怎么传导

```
src/overrides.py  ──┐
src/selection.json ┤
                    ├─→ build.py ─→ dist/*.conf ─→ release 分支 ─→ 订阅 URL
上游 lazy_group.conf ┘
```

上游变了、规格没变，产物也会变（这是设计目的）。所以「release 分支有新提交」不一定意味着有人改了规格——看提交信息和 Actions Summary 里的上游版本标记。

通用版不读取采样清单，稳定组保持空；个人速度最多 10 个，地区组包含全部合格节点，稳定组保留原三个。自动模式和混合模式的 AI / 谷歌只能走稳定组。不要用个人名字或名单污染通用候选池。

**一份源 → 两个客户端**：`dist/leon4z-fallback.conf` 是 Karing 渲染器的输入（中间表示），延续原个人版来源。加第三个客户端时照 `render_karing.py` 的样子再写一个渲染器，不要另起一套数据源。

## 权威状态从哪里取

要和小火箭 App 内的实际状态对齐时，注意 **`Documents/Backup/*.conf` 是过期导出**，权威来源是 iCloud Documents 目录下的解析缓存：

```
~/Library/Mobile Documents/iCloud~com~liguangming~Shadowrocket/Documents/
  <配置名>.conf--<hash>.db      ← sqlite，config 表 (section,name,value,option) 是生效配置
  shadowrocket.sync.plist       ← configs 段列出哪些配置是 alive（status: alive/deleted）
  Modules/                      ← 模块，规则优先级高于配置文件
```

节点名的权威来源是 Group Container（需要完全磁盘访问权限，ZCode 沙箱会报 `Operation not permitted`）：

```
~/Library/Group Containers/group.com.liguangming.Shadowrocket/ServerManager   ← NSKeyedArchiver，$objects 里是节点条目
```

注意：App 自己写节点名时会把 ASCII 部分**转成大写**（`🇸🇬Singapore 01` → `🇸🇬SINGAPORE 01`），`Backup/default-cn-copy.conf` 就是 App 自己生成的，可以对照。所以配置里出现全大写节点名不是笔误。

## 已知未验证项

见 README「已知取舍与未验证项」。核心两条：远程规则集文件内部的 `no-resolve` 是否被逐条 honor、`DOMAIN-SET` 的子域匹配语义——都没有实机验证，只依据上游文档与配置注释。改动这两块相关的东西时不要当成已确认事实。
