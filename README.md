# shadowrocket-config

这是小火箭配置的公开生成仓库。每天从 [johnshall 的 `lazy_group.conf`](https://github.com/johnshall/Shadowrocket-ADBlock-Rules-Forever) 取得上游配置，套用 `src/overrides.py` 的分流规格，校验后发布**通用版与个人版各三份配置**到 `release` 分支。只有个人版读取 `src/selection.json` 的试跑采样快照。仓库及发布的配置只含分组名称和节点名筛选正则；`[Proxy]` 保持为空。节点连接地址、密码和订阅 URL 由各设备的小火箭 App 单独管理。

## 两组、六份配置

| 模式 | 通用版文件 | 个人版文件 | 原 PROXY 分支 / FINAL | AI / 谷歌服务 |
| --- | --- | --- | --- | --- |
| 手动 | `Shadowrocket-select.conf` | `leon4z-select.conf` | 内置 `PROXY`，跟随首页选择 | 保留手动选项，默认 `PROXY` |
| 自动 | `Shadowrocket-fallback.conf` | `leon4z-fallback.conf` | `速度` fallback | **仅能选择 `稳定`** fallback |
| 混合 | `Shadowrocket-hybrid.conf` | `leon4z-hybrid.conf` | 内置 `PROXY`，跟随首页选择 | **仅能选择 `稳定`** fallback |

六份配置都包含 `速度`、`稳定` 和地区组，供手动选择。两种受众的候选来源不同：

| 组 | 通用版 | leon4z 个人版 |
| --- | --- | --- |
| 速度 | 上游各地区关键词匹配的节点并集，无采样保证、无 10 个上限 | 本地历史筛选并复测通过的跨地区节点，最多 **10 个**，尽量覆盖两个来源 |
| 国家 / 地区 | 沿用上游的地区名称、关键词和旗帜匹配 | 该地区**全部通过本次筛选与复测的节点**，取消每区 3 个上限；有合格节点才新增地区 |
| 稳定 | **默认空**，用户先指定自己的稳定节点 | 用户原先指定的 **3 个**，不按测速排名替换 |

`速度` 和 `稳定` 是 `fallback`：按组内顺序使用当前可用节点，失效后回退，并不保证选到延迟最低的节点。正则只限定候选池，排列不指定优先级；实际顺序由设备中的节点列表决定。地区组是 `url-test`。两类自动组都使用 HTTPS gstatic 204、600 秒间隔、5 秒超时；仅 `url-test` 有 100 毫秒 tolerance。自动和混合模式的 `AI` / `谷歌服务` 只有 `稳定` 一个选项。原有 `DIRECT` 选项、国内直连规则和规则顺序保留；原本以 `DIRECT` 为首项的服务组仍默认直连，不会强制全部服务代理。这里的手动版指服务出口由用户选择，用户也可主动选用某个自动组。

**旧地址的含义已调整：`Shadowrocket-*` 现在是通用版。使用个人采样名单的设备，请改订阅对应的 `leon4z-*` 地址。**

订阅地址：

```text
https://cdn.jsdelivr.net/gh/leon4z/shadowrocket-config@release/Shadowrocket-select.conf
https://cdn.jsdelivr.net/gh/leon4z/shadowrocket-config@release/Shadowrocket-fallback.conf
https://cdn.jsdelivr.net/gh/leon4z/shadowrocket-config@release/Shadowrocket-hybrid.conf
https://cdn.jsdelivr.net/gh/leon4z/shadowrocket-config@release/leon4z-select.conf
https://cdn.jsdelivr.net/gh/leon4z/shadowrocket-config@release/leon4z-fallback.conf
https://cdn.jsdelivr.net/gh/leon4z/shadowrocket-config@release/leon4z-hybrid.conf
```

每份配置内的 `update-url` 都指回自己的同名订阅地址。小火箭「配置」→「+」可导入链接；更新后仍需核对 App 正在使用的配置、节点订阅和实际命中策略。jsDelivr 与小火箭本身均可能缓存，仓库每天重建不等于设备立即切换。

## 通用版：先指定稳定节点

通用版不预设任何人的稳定节点，`稳定` 的筛选条件为 `(?!)`（不匹配任何名称）。使用自动或混合模式前，先配置这个组；手动版默认走首页选择，可先使用。空组在不同 Shadowrocket 版本中的实际流量行为仍需设备验证，不把它当成阻断或隐私保护机制。

推荐操作：下载配置后另存本地副本，删除或注释 `[General]` 的 `update-url`，再把 `稳定` 组的 `policy-regex-filter` 改成自己选中的节点名正则。保留组类型 `fallback`；不要直接省略过滤条件。比如两个虚构节点 `My Stable A`、`My Stable B` 对应 `(?i)^(?:My Stable A|My Stable B)$`。实际名称含正则特殊符号时需转义，逗号写为 `\x2c`。节点仍由 App 内的节点订阅管理，不填入公开的 `[Proxy]`。

远程配置更新会覆盖本地编辑；取消 `update-url` 后，远程规则集仍可更新，但整份配置结构不再跟随本仓库自动更新。这是 [LOWERTOP 社区手册的自动更新说明](https://github.com/LOWERTOP/Shadowrocket/wiki) 所描述的本地配置方式。需要整份配置自动更新的使用者，可 fork 仓库，在自己的规格中设置稳定筛选后自行发布；本仓库的通用稳定组会一直保持空。扩展配置的同名组覆盖语义尚未实机确认，不据此承诺保留手动成员。

## 个人版：采样快照的边界

`src/selection.json` 是独立采集器产生的公开、无连接信息清单。它给个人版 `速度` 和各地区组提供**精确锚定的节点名正则**。试跑从最近 24 小时已完成轮次中筛选：至少三轮、覆盖至少 30 分钟、最近三轮齐全、历史有效目标全部成功、成功请求 P95 小于 5 秒，且订阅源健康、刷新不超过 36 小时。候选还须匹配当前客户端连接身份与名称，排除名称冲突和敏感名称；发布前按当前客户端参数复测 gstatic、Cloudflare、GitHub 三个 HTTPS 目标。速度池按 P95 排序取最多 10 个并尽量保留来源多样性，地区池保留全部合格者。“全部”指通过这些门槛，并非只要某一次连通过就纳入。一次探测通过不能证明 AI 账号、地区解锁或长连接可用。

清单必须包含版本、`trial` 模式、生成时间、采样窗口、至少三轮、selection ID 和各组的精确正则及节点数。生成器拒绝空组、宽泛正则、非法名称、逗号/换行、缺少既有地区组以及个人速度超过 10 个等情况；缺清单时个人版构建失败，不退回全节点。通用版可独立生成，不读取此文件。

当前清单只代表其 header 中标出的**试跑采样窗口**，不能称为满 24 小时或长期稳定性结论。每天的构建会继续使用这同一份时间标注的快照；**自动刷新清单尚未启用**。节点改名、订阅变化或原有稳定组三节点失效，都可能使组变空或与实际设备不符，需重新采样并复核。`稳定` 在 `src/overrides.py` 按现有主线、备用和第三节点的名称标签做整行匹配，发布前确认当前仅命中 3 个。两个名称含私有地址，公开正则只保留地址格式，不包含实际地址；以后同标签重复、节点改名仍需重新核对，不能保证任意订阅变化后永远恰好三个。

配置本身不能证明小火箭已完成真实流量切换。短 HTTPS 探测也不能覆盖视频吞吐、长连接、地区限制或每台设备的实时网络；设备端仍负责当下可用性判断。

## 构建与校验

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
python3 scripts/build.py
python3 scripts/build.py --no-network
python3 scripts/build.py --audience generic --out-dir dist/generic-only
```

`--no-network` 需要之前留在 `dist/cache/` 的上游配置，仅用于离线检查。完整构建先核对上游结构锚点，再生成六变体，检查服务组和 `FINAL`、`AI` / `谷歌服务` 严格边界、受众各自的候选正则、组引用及循环、空 `[Proxy]`、同名更新 URL；之后联网逐个校验远程规则集。任一步失败，CI 不发布，`release` 保持上一版。单元测试使用合成规格，不依赖真实采样或节点凭据，包含移除个人数据后的通用版独立构建。

当前 CI 把六份配置作为一次完整发布：个人清单无效时，通用版也保留上一版。这里的“通用不依赖个人采样”指配置内容和独立构建能力；尚未拆成两条可分别发布的流水线。需要单独采用通用版时使用 `--audience generic`。

规则集仍沿用原有转换：四处 Quantumult X 列表切换到小火箭版，其中拆分的域名集用 `DOMAIN-SET` 补全，缺失的通配域名用 `DOMAIN-WILDCARD` 显式补全。远程规则集 URL 从 GitHub raw 改写为 jsDelivr，构建时校验类型、空文件和 IP 规则的 `no-resolve`。jsDelivr 的分支缓存可能滞后，发布后工作流会尝试清理缓存。

Karing 渲染器跟随原个人版，输入改为 `leon4z-fallback.conf`；其动作与小火箭的策略组语义并非完全相同，不能把这六份配置视为六份 Karing 配置。

## 已知取舍与未验证项

小火箭对远程规则集内部 `no-resolve` 是否逐条生效，以及 `DOMAIN-SET` 的子域匹配语义，仍需设备端验证。远程配置更新会覆盖设备内对该配置的本地编辑；有本地改动时应另存配置。

## 为什么不直接存一份配置

上游 `lazy_group.conf` 会持续演进：新增服务分类、调整规则顺序、改注释。存一份拷贝的话这些改进永远进不来，只有规则集内容会跟着上游变——这是「分叉一份配置自己维护」的通病。

所以这里存的是**规格**：每天重新拉上游、重新套用差异。上游改了什么都会自动跟进来；如果上游把规格依赖的锚点改掉了（比如重命名了某个分组），构建会**显式失败**并列出缺哪个锚点，而不是静默产出一份错的配置。

## 覆盖规格改了什么

分流规格在 `src/overrides.py`，采样名单在 `src/selection.json`。以下保留既有规则差异说明；具体条目数是原验收时的历史记录，以最新构建报告为准。

### 1. 规则集方言：4 处圈X 引用 → 小火箭版

上游这四个分类引用的是 `rule/QuantumultX/` 下的文件，里面的 IP 规则**不带 `no-resolve`**。小火箭遇到不带 `no-resolve` 的 IP 规则，会为了判断域名是否命中而发起本地 DNS 查询。

逐条比对过两版的差异（口径：圈X 的 `HOST-*` ↔ 小火箭的 `DOMAIN-*`）：

| 分类 | 域名规则 | IP 规则 | IP 集合是否逐条相同 | 缺 no-resolve |
| --- | --- | --- | --- | --- |
| China | 3732 → 3732（完全一致） | 21 → 21 | 是 | 21 → **0** |
| WeChat | 32 → 32（完全一致） | 1 → 1 | 是 | 0 → 0 |
| Apple | 1862 → 1605 | 13 → 13 | 是 | 13 → **0** |
| Global | 35666 → 34976 | 116 → 116 | 是 | 116 → **0** |

两版的 IP 规则集合逐条相同，**唯一差别就是小火箭版每条都带 `no-resolve`**。换过来之后全配置 1436 条 IP 规则 100% 带 `no-resolve`。

Apple 少掉的 257 条域名全部是小火箭版里已有父级后缀覆盖的子域（无兜底 0 条）。Global 少掉的 700 条里有 25 条没有同类兜底，其中 24 条是 `google.com.XX` 国家域——它们会落到 `FINAL`，策略和原来一致。

**唯一真实的路由变化**：上游小火箭版 Global 里新增了 10 条国内域名，会从「China→直连」变成「Global→代理」：

```
futu.cn  futubull.cn  jinrieluosi.cn  longbridge.cn  longportapp.cn
schwab.com.cn  skytigris.cn  steamconnecttest.com  tigerbbs.cn  zhijianfengyi.cn
```

这是上游有意加的（富途、长桥、嘉信这类跨境券商域名），不是方言转换的副作用。不想要就在 `src/overrides.py` 的 `RULESET_DIALECT` 之外加一条直连覆盖。

另外小火箭版把域名拆到了 `X_Domain.list`（裸域名，带前导点），必须用 `DOMAIN-SET` 引用——只换 `RULE-SET` 那一行会丢掉全部域名规则（Apple 丢 1560 条、China 丢 3689 条、Global 丢 34895 条）。圈X 版里的 15 + 1 条 `HOST-WILDCARD` 在小火箭版没有对应写法，用小火箭原生的 `DOMAIN-WILDCARD` 显式补回来。

### 1b. `[General]` 覆盖：关闭 IPv6

上游是 `ipv6 = true`（同时查 A 和 AAAA 记录），改成 `false`，避免 IPv6 出口不通时走 IPv6 导致连接卡住或分流异常。`prefer-ipv6` 上游本来就是 `false`，不动。

规则写在 `GENERAL_OVERRIDES` 里。上游没有这个键时会追加到 `[General]` 段末，所以不依赖上游一定保留它；生成后有自检确认覆盖真的生效了，没生效就构建失败。

⚠️ 手册的补充值得知道：**即使 `ipv6 = false`，「当本地网络环境支持 IPv6，并且节点域名支持 IPv6 解析，Shadowrocket 也会使用节点的 IPv6 地址进行访问」**。要彻底避免，需要关闭节点域名的 IPv6 解析，或者在 `[Host]` 段给节点域名指定 IPv4 地址。这一层目前没做——如果你发现 IPv6 仍然在走，就是这里。

### 2. 分组调参

- **所有组的 `tolerance` 统一改成 100**（上游是 0 混着的）。含义是：只有新优胜者的延迟比旧优胜者低出 100ms 以上，才切换节点。这是 `url-test`「择优」用的参数——所以作用于采样清单中的国家组；`select` 不测速、`fallback` 按可用性切换，两者都不涉及，不给它们新增。
- **删掉所有 `policy-select-name`**，回到上游的位置默认机制（`select=0` = 成员列表里的第 1 个）。按名字指定默认项的问题是名字写错了也看不出来，位置默认至少行为一致。

### 2b. 规则集走 jsDelivr 而不是 raw.githubusercontent.com

小火箭在设备上要拉全部 37 个规则集。全指向 `raw.githubusercontent.com` 时，本机实测（网络通畅）总耗时 **93.8 秒**，其中两个 DOMAIN-SET 直接 **30 秒超时**：

```
37 个文件合计 0.71 MB，总耗时 93.8s
30.01s  URLError  Shadowrocket/Apple/Apple_Domain.list
30.00s  URLError  Shadowrocket/China/China_Domain.list
 3.35s  537KB     Shadowrocket/Global/Global_Domain.list
```

体积只有 0.71 MB，所以**不是大小问题，是 37 次独立请求打在 raw 上的可靠性问题**——raw 对短时间大量请求会限流，国内环境更差。改走 jsDelivr 后同样的 37 个文件：

```
jsDelivr 拉全部 37 个：合计 0.79MB，总耗时 26.6s，失败 0 个
```

在设备上更新配置时这个问题更明显：**这份配置新引入了 7 个设备上没有缓存的规则集**（4 个小火箭版 + 3 个 `_Domain.list`），必须现拉，正好打在 raw 最不稳的地方——表现就是「更新订阅超时」。

改写成通用规则而不是逐个列白名单：

```
https://raw.githubusercontent.com/<owner>/<repo>/<ref>/<path>
→ https://cdn.jsdelivr.net/gh/<owner>/<repo>@<ref>/<path>
```

所以上游以后引用新的 raw 仓库也能自动接住；万一某个仓库 jsDelivr 不服务，构建时的远程校验会失败并点名是哪个。想换回 raw 就把 `USE_JSDELIVR_FOR_RULESETS` 设成 `False`。

注意 jsDelivr 对分支引用有缓存（最长十几小时），所以规则集内容可能比上游晚半天。规则集本身每日更新，这个延迟可以接受；要立刻生效可以手动 purge。


## 和本地配置的关系

若要在 App 里手动微调配置，请参考上面的通用版步骤保存本地副本。远程配置更新会覆盖本地修改；社区手册列出了以下自定义方式：

- **删掉/注释掉 `update-url = *`**：配置变成「本地配置」，自动更新只刷新规则集，不动配置本身。
- **用扩展配置/包含配置**（`include = `，配置文件 ⓘ > 通用 > 包含配置）：b 包含 a，b 优先级更高，自定义放 b。

本仓库走的是第三条路（上游 + 差异，每日重建），好处是不依赖 `include` 的合并语义（手册没写 `[Proxy Group]` 是按名覆盖还是追加），也不需要在设备上多挂一份配置。

## Karing（Clash / sing-box 系客户端）

同一份源也渲染出一套 Karing 能用的产物，不用在 Karing 里另外手配一遍：

```
karing/diversion_rules_custom.json          导入用（Karing 的「分流分组」JSON）
karing/ruleset/<分类>.json                  29 个 sing-box 源码规则集，JSON 里按 URL 引用
```

地址：`https://cdn.jsdelivr.net/gh/leon4z/shadowrocket-config@release/karing/diversion_rules_custom.json`

**导入方式**：Karing 的导入是**本地文件选择器**（`DiversionCustomRules.getFromFile`），不支持 URL，所以先把这个 JSON 下载到本地，再走「分流规则 → 自定义分组 → 右上菜单 → 导入」。导入一次即可——**真正每天变的是它引用的那 29 个规则集，那些是按 URL 自动更新的**；只有分类结构变了才需要重新导入。

### 动作映射

Karing 的规则动作只有四种（`direct` / `block` / `urltest` / `currentSelected`），**不能像小火箭那样给每个服务指定不同分组**。所以映射是：

| 小火箭策略 | Karing 动作 |
| --- | --- |
| `DIRECT` | `direct` |
| `REJECT*` | `block` |
| `速度`（默认代理组） | `urltest`（Karing 的「自动选择」） |
| `稳定`（例外代理组） | `currentSelected`（Karing 的「当前选择」） |

**这意味着「AI 走稳定、其余走速度」在 Karing 里只能做成两档**：AI/谷歌那几条走 `currentSelected`，需要你在首页手动选合适的稳定出口；其余走 `urltest`，节点池用「服务器选择关键词」限定成 `速度` 那批。

规则集是从 blackmatrix7 的列表机械转换的（`DOMAIN-SUFFIX`→`domain_suffix`、`DOMAIN-WILDCARD`→`domain_regex`、`IP-CIDR`→`ip_cidr`）。三种类型在 sing-box 规则集里没有对应字段，会被丢弃并在构建输出里点名：`USER-AGENT`（130 条）、`IP-ASN`（7 条）、`URL-REGEX`（1 条）。

兜底用 Karing 的内置集表达：`GEOIP,CN,DIRECT` → `acl:ChinaIp` 等，`FINAL` → `geosite:geolocation-!cn` + `acl:ProxyGFWlist` + `acl:ProxyMedia`。

### 一个待验证的测试规则

JSON 末尾有一条**默认关闭**的 `🧪 测试-分组定向`，它的 `outbound` 直接写分组名 `稳定` 而不是四个常量之一。Karing 的界面代码只对四个常量做显示映射，所以这条是为了验证：**Karing 到底接不接受任意分组名**。如果接受，分组粒度就能做满，上面那个「只能两档」的限制就不存在了。测试办法：导入后把它打开，看它显示成什么、以及分流规则检测里 `claude.ai` 命中它之后走的是哪个出口。


## 上游依赖

- 配置骨架：[johnshall/Shadowrocket-ADBlock-Rules-Forever](https://github.com/johnshall/Shadowrocket-ADBlock-Rules-Forever) 的 `lazy_group.conf`（`release` 分支）
- 规则集：[blackmatrix7/ios_rule_script](https://github.com/blackmatrix7/ios_rule_script) 的 `rule/Shadowrocket/*`
- AI 规则集：[iab0x00/ProxyRules](https://github.com/iab0x00/ProxyRules)
