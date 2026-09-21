# shadowrocket-config

个人小火箭（Shadowrocket）配置仓库：源文件在这里维护，GitHub Actions 每天校验一次所有远程规则集，把通过的版本发布到 `release` 分支，小火箭通过一个 URL 订阅。

节点不在这个仓库里，也不在配置文件里。配置的 `[Proxy]` 段是空的，节点由小火箭 App 内的订阅管理——这个仓库只负责「分流规则 + 策略组」。

## 订阅地址

```
https://raw.githubusercontent.com/leon4z/shadowrocket-config/release/Shadowrocket.conf
```

国内直连 `raw.githubusercontent.com` 通常不通，改用 jsDelivr 镜像（内容相同）：

```
https://cdn.jsdelivr.net/gh/leon4z/shadowrocket-config@release/Shadowrocket.conf
```

小火箭里：底部「配置」→ 右上「+」→ 粘贴上面的 URL → 下载后长按该配置 → 选中使用。

> ⚠️ 一旦改用远程订阅，**在小火箭 App 里对这份配置的修改会在下次更新时被覆盖**。要改规则请改本仓库的 `src/Shadowrocket.conf`，commit 后 Actions 会自动重新校验并发布。如果只是想临时试试，建议先另存一份本地副本。

## 日常怎么改

只改 `src/Shadowrocket.conf`，然后 commit 到 `main`。工作流会：

1. 把配置里所有 `RULE-SET` / `DOMAIN-SET` 的远程文件拉下来逐条校验（类型名是否认识、是否为空、IP 规则有没有 `no-resolve`）；
2. 检查 `[Rule]` 段引用的策略名是否真的在 `[Proxy Group]` 里定义过，以及每个分组的 `policy-regex-filter` 是否是合法正则；
3. 全部通过才发布到 `release`。

**校验失败就不发布**，`release` 分支停在上一版，所以上游改名 / 断供 / 改格式不会直接打到你在用的配置上。失败原因在 Actions 运行页的 Summary 里，同时会列出每个规则集的条目数。

本地也可以先跑一遍：

```bash
python3 scripts/build.py                      # 校验 + 生成 dist/Shadowrocket.conf
python3 scripts/build.py --no-network         # 只做本地解析检查，不联网
python3 scripts/build.py --out -              # 结果打到 stdout
```

## 与基线配置的差异

基线 = 2026-09-21 00:52 App 内「稳定速度-本地定制」的实时状态（`Backup/` 里那份 9-20 22:03 的导出是过期的，`速度` 组少两个节点）。相对基线只有三类改动：

### 1. `速度` / `稳定` 两个组改为正则筛选

原来写死了节点名：

```
速度 = url-test,新加坡-SG-2-:1,新加坡-SG-1-:1,🇸🇬SG 01,🇸🇬SG 04 电信2X,…
稳定 = fallback,美国 GRANDE&RCN 66.167.174.200 · 主线,…,BZ-VMESS-TLS,…
```

现在按名称正则筛：

```
速度 = url-test,policy-regex-filter=🇸🇬|SG|Singapore|新加坡|狮城|沪新|京新|深新|杭新|广新|🇲🇾|Malaysia|马来|马来西亚,…
稳定 = fallback,policy-regex-filter=Grande|GRANDE|BZ-VMess|BZ-VMESS,…
```

原因有两条。一是订阅换代后写死的名字会失效。二是**写死的名字里大小写和实际节点对不上**：配置里是 `🇸🇬SG 04 电信2X`、`🇲🇾MALAYSIA 02`、`BZ-VMESS-TLS`，App 里实际节点名是 `🇸🇬SG 04 电信2x`、`🇲🇾Malaysia 02`、`BZ-VMess-TLS`。小火箭对分组员名的大小写是否敏感没有实测确认（`[Rule]` 里写 `YOUTUBE`、分组名却是 `YouTube` 是能正常工作的，说明策略名查找不区分大小写，但组员匹配不一定走同一段代码）。改成正则后这个不确定性就不存在了。

两条正则都拿本机 115 个真实节点名验证过：`速度` 命中 14 个（原有 10 个 + 另一个订阅里的 4 个 `🇸🇬Singapore 0N`），`稳定` 精确命中原来的 3 个。

副作用：`速度` 组现在包含订阅里**所有**东南亚节点，包括没测过速的。要恢复「筛出来但默认选中某个」，在行尾加 `policy-select-name=节点名` 即可（基线里本来就有 `policy-select-name=🇲🇾MALAYSIA 02`，按你选的方案没保留）。

⚠️ **`稳定` 组这条正则仍然依赖供应商标签**（`Grande`、`BZ-VMess`），供应商改名后这个组会变空，那 AI 和谷歌服务就没有可用出口了。想彻底免疫就换成按地区筛，代价是会包含订阅里所有美国节点：

```
稳定 = fallback,policy-regex-filter=🇺🇸|US|USA|United States|美国,timeout=5,interval=600,url=http://www.gstatic.com/generate_204
```

另外，正则会出现在公开仓库里，所以 `Grande`、`BZ-VMess` 这两个标签是公开的（只是名字，不含地址和密钥）。介意的话换成地区筛即可。

### 2. 4 处 QuantumultX 版规则集引用改为 Shadowrocket 版

上游 `johnshall/Shadowrocket-ADBlock-Rules-Forever` 的 `lazy_group.conf` 里，Apple / WeChat / Global / China 四个分类引用的是 `rule/QuantumultX/` 下的文件，这些文件的 IP 规则**不带 `no-resolve`**。小火箭遇到不带 `no-resolve` 的 IP 规则，会为了判断域名是否命中而发起本地 DNS 查询。

逐条比对过两版的差异（口径：QX 的 `HOST-*` ↔ SR 的 `DOMAIN-*`）：

| 分类 | 域名规则 | IP 规则 | IP 集合是否逐条相同 | 缺 no-resolve |
| --- | --- | --- | --- | --- |
| China | 3732 → 3732（完全一致） | 21 → 21 | 是 | 21 → **0** |
| WeChat | 32 → 32（完全一致） | 1 → 1 | 是 | 0 → 0 |
| Apple | 1862 → 1605 | 13 → 13 | 是 | 13 → **0** |
| Global | 35666 → 34976 | 116 → 116 | 是 | 116 → **0** |

两版的 IP 规则集合逐条相同，**唯一差别就是 SR 版每条都带 `no-resolve`**。换成 SR 版后全配置 1436 条 IP 规则 100% 带 `no-resolve`。

Apple 少掉的 257 条域名全部是 SR 版里已有父级后缀覆盖的子域（无兜底 0 条）。Global 少掉的 700 条里有 25 条没有同类兜底，其中 24 条是 `google.com.XX` 国家域——它们会落到 `FINAL,速度`，策略和原来一致（原来就是 Global→速度）。

**唯一真实的路由变化**：上游 SR 版 Global 里新增了 10 条国内域名，会从「China→直连」变成「Global→速度（代理）」：

```
futu.cn  futubull.cn  jinrieluosi.cn  longbridge.cn  longportapp.cn
schwab.com.cn  skytigris.cn  steamconnecttest.com  tigerbbs.cn  zhijianfengyi.cn
```

这是上游有意加的（富途、长桥、嘉信这类跨境券商域名），不是方言转换的副作用。不想要的话在 `[Rule]` 里 Global 那两行之前加 `DOMAIN-SUFFIX,<域名>,DIRECT` 即可。

### 3. 补 DOMAIN-SET 与 16 条 DOMAIN-WILDCARD

Shadowrocket 版把域名拆成了两个文件：`X.list` 只放 IP / USER-AGENT / 关键词规则，几万条域名在 `X_Domain.list` 里，必须用 `DOMAIN-SET` 引用。只换 `RULE-SET` 那一行不补 `DOMAIN-SET` 会丢掉全部域名规则（Apple 丢 1560 条、China 丢 3689 条、Global 丢 34895 条），所以三处都补了。

QX 版的 Apple 和 China 里另有 15 + 1 条 `HOST-WILDCARD` 规则（`apple.*`、`iphone.*`、`macbookpro.*`、`windows-*.net` 等），SR 版没有对应写法，因此在配置里显式写成 `DOMAIN-WILDCARD`（这是小火箭原生类型，在配置文件规则行的白名单正则里）。

## 已知取舍与未验证项

- **`no-resolve` 对 China 分类没有副作用**，因为配置里 `GEOIP,CN,DIRECT` 紧随其后，CN 的 IP 仍然会被兜住。
- **没有实测**小火箭是否真的逐条 honor 远程规则集文件内部的 `no-resolve`。上游 README 和配置文件注释都指向「是」，但没法在没有设备的情况下跑实验。自测办法见下。
- **`DOMAIN-SET` 的匹配语义**（是否含子域）没有实测，只按上游官方建议的 `X.list` + `X_Domain.list` 配对写法使用。
- 规则集直接引用上游 raw（未镜像到本仓库），所以上游改目录结构、改格式或 raw 被墙时，会在下一次构建失败。这是刻意的取舍：配置保持轻量，规则永远跟随上游最新。
- 仓库里不含任何节点地址、密码或订阅地址。

## 自测办法

小火箭里：配置详情 →「测试规则」→ 输入域名，看命中哪条策略。建议试：

| 输入 | 期望 |
| --- | --- |
| `iphone.com` | 命中 `DOMAIN-WILDCARD,iphone.*` → 苹果服务（验证通配规则生效） |
| `www.apple.com` | 苹果服务（验证 `DOMAIN-SET` 的 `Apple_Domain.list` 生效） |
| `www.baidu.com` | DIRECT（验证 `China_Domain.list` 生效） |
| `www.google.com` | 谷歌服务（验证 Global 之前的规则仍然优先） |
| `futu.cn` | 速度（这是上面说的那 10 条变化之一） |

## 目录结构

```
src/Shadowrocket.conf          配置源文件，唯一需要手工维护的东西
scripts/build.py               校验 + 生成
dist/                          本地构建产物（不提交）
.github/workflows/build.yml    每日 UTC 23:00 / push 时构建，发布到 release
```

`release` 分支只放一个 `Shadowrocket.conf`，是给订阅 URL 用的。

## 上游依赖

- 规则集：[blackmatrix7/ios_rule_script](https://github.com/blackmatrix7/ios_rule_script)（`rule/Shadowrocket/*`）
- AI 规则集：[iab0x00/ProxyRules](https://github.com/iab0x00/ProxyRules)
- 配置骨架与分组思路：[johnshall/Shadowrocket-ADBlock-Rules-Forever](https://github.com/johnshall/Shadowrocket-ADBlock-Rules-Forever) 的 `lazy_group.conf`
