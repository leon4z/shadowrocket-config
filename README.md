# shadowrocket-config

个人小火箭（Shadowrocket）配置：**不存配置拷贝，存的是「上游 + 差异」**。每天从上游拉一次 `lazy_group.conf`，套用本仓库的覆盖规格，产出两份配置发布到 `release` 分支，小火箭通过链接订阅。

节点不在这个仓库里，也不在配置里（`[Proxy]` 段是空的）——节点由小火箭 App 内的订阅管理。这里只管分流规则和策略组。

## 两份配置

| 文件 | 出口怎么定 | 什么时候用 |
| --- | --- | --- |
| `Shadowrocket-select.conf` | 上游原版逻辑：服务组都是 `select`，指向内置 `PROXY`，**出口由你在首页手动选** | 想完全跟着上游走、自己控制出口的场景 |
| `Shadowrocket-fallback.conf` | 服务组指向自建的 `速度` / `稳定` 组，两者都是 `fallback`，**自动故障转移** | 想让 AI/谷歌走美国组、其余走东南亚组，且节点挂了自动切 |

两者除了下面列的差异，其余完全相同（同一份上游、同一套规则集、同样的 tolerance）。

```
https://raw.githubusercontent.com/leon4z/shadowrocket-config/release/Shadowrocket-select.conf
https://raw.githubusercontent.com/leon4z/shadowrocket-config/release/Shadowrocket-fallback.conf
```

国内直连 `raw.githubusercontent.com` 通常不通，用 jsDelivr 镜像（内容相同）：

```
https://cdn.jsdelivr.net/gh/leon4z/shadowrocket-config@release/Shadowrocket-select.conf
https://cdn.jsdelivr.net/gh/leon4z/shadowrocket-config@release/Shadowrocket-fallback.conf
```

> ⚠️ **jsDelivr 对分支引用有缓存**，不清的话镜像可能滞后十几小时。所以流水线在每次发布后会调 `purge.jsdelivr.net` 清这两个文件的缓存。如果你手动改了 release 分支或想立刻生效，可以自己清一次：
> ```bash
> curl "https://purge.jsdelivr.net/gh/leon4z/shadowrocket-config@release/Shadowrocket-fallback.conf"
> ```
> 另外小火箭自己的自动更新间隔是 **1–7 天**（设置 > 自动更新 > 配置 > 更新间隔），所以就算这边每天构建，App 也是按它自己的节奏拉取。

小火箭里：底部「配置」→ 右上「+」→ 粘贴 URL → 下载后长按该配置 → 选中使用。

> ⚠️ 远程配置一旦更新，会**覆盖在 App 内对该配置所做的修改**（手册「更新配置」一节明确写了）。所以这两份配置不要在 App 里改，要改就改本仓库的 `src/overrides.py`。如果你的用法是「在 App 里手动微调」，那应该改用本地配置（见下面「和本地配置的关系」）。

## 为什么不直接存一份配置

上游 `lazy_group.conf` 会持续演进：新增服务分类、调整规则顺序、改注释。存一份拷贝的话这些改进永远进不来，只有规则集内容会跟着上游变——这是「分叉一份配置自己维护」的通病。

所以这里存的是**规格**：每天重新拉上游、重新套用差异。上游改了什么都会自动跟进来；如果上游把规格依赖的锚点改掉了（比如重命名了某个分组），构建会**显式失败**并列出缺哪个锚点，而不是静默产出一份错的配置。

## 覆盖规格改了什么

全部改动都在 `src/overrides.py`，逐项都有注释。三类：

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

### 2. 分组调参

- **所有组的 `tolerance` 统一改成 100**（上游是 0 混着的）。含义是：只有新优胜者的延迟比旧优胜者低出 100ms 以上，才切换节点。这是 `url-test`「择优」用的参数——所以只作用于上游那 6 个国家组；`select` 不测速、`fallback` 按可用性切换，两者都不涉及，不给它们新增。
- **删掉所有 `policy-select-name`**，回到上游的位置默认机制（`select=0` = 成员列表里的第 1 个）。按名字指定默认项的问题是名字写错了也看不出来，位置默认至少行为一致。

### 3. 自建组（只用于 `Shadowrocket-fallback.conf`）

新增两个组，并把上游指向内置 `PROXY` 的地方改指向它们：

```
速度 = fallback,policy-regex-filter=🇸🇬|SG|Singapore|新加坡|狮城|沪新|京新|深新|杭新|广新|🇲🇾|Malaysia|马来|马来西亚,interval=600,timeout=3,url=http://www.gstatic.com/generate_204
稳定 = fallback,policy-regex-filter=Grande|GRANDE|BZ-VMess|BZ-VMESS,interval=600,timeout=5,url=http://www.gstatic.com/generate_204
```

替换规则：**默认把所有 `PROXY` 换成 `速度`，`AI` 和 `谷歌服务` 两处换成 `稳定`**。用模式匹配而不是行号，所以上游以后新增的服务分组只要指向 `PROXY`，会自动一起改。

两条正则都拿本机 115 个真实节点名验证过命中集合：`速度` 命中 14 个（含另一个订阅的 4 个 `🇸🇬Singapore 0N`），`稳定` 精确命中原来的 3 个。

**两个组都是 `fallback`，不配 `tolerance`。** `fallback` 的语义是「节点不可用时切到其他可用节点，可用范围由上次测试结果决定」，它按可用性切换、不做择优比较，所以 `tolerance` 对它是无效参数（手册里 `tolerance` 的定义是「只有当新优胜者的分数高于旧优胜者加公差时才换线」，「优胜者」是 `url-test` 的概念）。对比一下：

- `url-test`：自动切换**延迟最低**的节点——会为了快而换线。
- `fallback`：只在这一档不可用时才换——稳定优先。

⚠️ **`fallback` 取的是「筛出来的第一个可用节点」，所以成员顺序就是优先级。** 用正则筛选时，顺序是订阅里节点的排列顺序，不是人工指定的顺序。如果你希望优先用某几个节点，要么改成显式成员列表（按优先级排列），要么加 `policy-select-name=<节点名>` 指定默认选中项。

上游的 `select` 服务组保持不变——所以「出口类别」是你定的（AI 走稳定、YouTube 走速度），「类别内部」按 `fallback` 的可用性逻辑自动切换。

⚠️ **`稳定` 组这条正则绑在供应商标签上**（`Grande`、`BZ-VMess`），供应商改名后这个组会变空，AI 和谷歌服务就没有可用出口了。想彻底免疫就换成按地区筛，代价是包含订阅里所有美国节点：

```
稳定 = fallback,policy-regex-filter=🇺🇸|US|USA|United States|美国,...
```

另外这两个标签会出现在公开仓库里（只是名字，不含地址和密钥）。介意的话换成地区筛。

## 和本地配置的关系

如果你更想在 App 里手动微调配置，那**不要用这里的订阅**——远程配置的更新会覆盖本地修改。手册 `自动更新` 一节给了两种「既要自动更新又不丢自定义」的官方做法：

- **删掉/注释掉 `update-url = *`**：配置变成「本地配置」，自动更新只刷新规则集，不动配置本身。
- **用扩展配置/包含配置**（`include = `，配置文件 ⓘ > 通用 > 包含配置）：b 包含 a，b 优先级更高，自定义放 b。

本仓库走的是第三条路（上游 + 差异，每日重建），好处是不依赖 `include` 的合并语义（手册没写 `[Proxy Group]` 是按名覆盖还是追加），也不需要在设备上多挂一份配置。

## 日常怎么改

只改 `src/overrides.py`，然后 commit 到 `main`。工作流会：

1. 拉上游 `lazy_group.conf`，检查锚点；
2. 生成两个变体，自检「不该再有圈X 引用」「custom 变体不该再有裸 `PROXY` 策略」；
3. 把配置里所有远程规则集拉下来逐条校验（类型名是否认识、是否为空、IP 规则有没有 `no-resolve`）；
4. 检查 `[Rule]` 引用的策略名是否真的在 `[Proxy Group]` 里定义过，以及每个 `policy-regex-filter` 是不是合法正则；
5. 全部通过才发布到 `release`。

**任一步失败就不发布**，`release` 停在上一版。失败原因在 Actions 运行页的 Summary 里，同时列出每个规则集的条目数。

本地也可以先跑：

```bash
python3 scripts/build.py                    # 拉上游 + 校验 + 生成两份配置
python3 scripts/build.py --no-network       # 用上次抓到的上游缓存，不联网
python3 scripts/build.py --out-dir /tmp/x   # 换输出目录
```

输出是 (上游内容, 规格) 的**纯函数**——同样输入逐字节产出同样结果，上游和规格都没变就不产生新提交。

## 已知取舍与未验证项

- **没有实测**小火箭是否真的逐条 honor 远程规则集文件内部的 `no-resolve`。上游 README 和配置注释都指向「是」，但没法在没有设备的情况下跑实验。自测办法见下。
- **`DOMAIN-SET` 的匹配语义**（是否含子域）没有实测，只按上游官方建议的 `X.list` + `X_Domain.list` 配对写法使用。
- `no-resolve` 对 China 分类没有副作用，因为配置里 `GEOIP,CN,DIRECT` 紧随其后，CN 的 IP 仍然会被兜住。
- 规则集直接引用上游 raw（未镜像到本仓库），所以上游改目录结构或 raw 被墙时会在下一次构建失败。这是刻意的：配置保持轻量，规则永远跟随上游最新。
- 仓库里不含任何节点地址、密码或订阅地址。`稳定` 组正则里的 `Grande`、`BZ-VMess` 是节点名片段（见上）。

## 自测办法

小火箭里：配置详情 →「测试规则」→ 输入域名，看命中哪条策略。建议试：

| 输入 | 期望 |
| --- | --- |
| `iphone.com` | 苹果服务（验证 `DOMAIN-WILDCARD` 通配规则生效） |
| `www.apple.com` | 苹果服务（验证 `DOMAIN-SET` 的 `Apple_Domain.list` 生效） |
| `www.baidu.com` | DIRECT（验证 `China_Domain.list` 生效） |
| `www.google.com` | 谷歌服务（验证 Global 之前的规则仍然优先） |
| `futu.cn` | 速度（`Shadowrocket-fallback.conf`；这是上面说的那 10 条变化之一） |

再确认一下 `Shadowrocket-fallback.conf` 里两个组真的筛到了节点：配置详情 →「代理分组」，看 `速度` 有几个成员、`稳定` 是不是 3 个。

## 目录结构

```
src/overrides.py               覆盖规格，唯一需要手工维护的东西
scripts/build.py               拉上游 + 套规格 + 校验 + 生成
scripts/summary.py             把构建报告渲染成 Actions 运行摘要
dist/                          本地构建产物（不提交）
.github/workflows/build.yml    每日 UTC 23:00 / push 时构建，发布到 release
```

`release` 分支只放两份配置，供订阅 URL 使用。

## 上游依赖

- 配置骨架：[johnshall/Shadowrocket-ADBlock-Rules-Forever](https://github.com/johnshall/Shadowrocket-ADBlock-Rules-Forever) 的 `lazy_group.conf`（`release` 分支）
- 规则集：[blackmatrix7/ios_rule_script](https://github.com/blackmatrix7/ios_rule_script) 的 `rule/Shadowrocket/*`
- AI 规则集：[iab0x00/ProxyRules](https://github.com/iab0x00/ProxyRules)
