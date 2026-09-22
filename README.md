# Shadowrocket Config

基于 [johnshall 懒人配置](https://github.com/johnshall/Shadowrocket-ADBlock-Rules-Forever) 的 Shadowrocket 分流配置，提供手动选择、自动故障切换和混合分流三种通用模式，支持按服务和地区选择出口。

本仓库提供分流规则与策略组，不提供代理节点。使用前，请先在 Shadowrocket 中添加自己的节点或节点订阅。

## 选择配置

| 模式 | 普通代理流量 | AI / Google 服务 | 配置链接 |
| --- | --- | --- | --- |
| 手动选择 | 跟随首页所选节点或策略组 | 跟随首页选择，可在服务组中单独调整 | [Shadowrocket-select.conf](https://cdn.jsdelivr.net/gh/leon4z/shadowrocket-config@release/Shadowrocket-select.conf) |
| 自动切换 | 使用「速度」组自动故障切换 | 使用「稳定」组自动故障切换 | [Shadowrocket-fallback.conf](https://cdn.jsdelivr.net/gh/leon4z/shadowrocket-config@release/Shadowrocket-fallback.conf) |
| 混合分流 | 跟随首页所选节点或策略组 | 使用「稳定」组自动故障切换 | [Shadowrocket-hybrid.conf](https://cdn.jsdelivr.net/gh/leon4z/shadowrocket-config@release/Shadowrocket-hybrid.conf) |

三种模式都保留上游的国内直连规则与服务分类。表中描述的是代理流量；原本默认直连的服务不会因此全部改走代理。

**自动切换和混合分流模式需要先设置「稳定」组。该组默认没有节点。**

## 开始使用

1. 在 Shadowrocket 中添加可用节点或节点订阅。
2. 复制上表所需配置的链接，在「配置」页通过「+」导入。
3. 若选择自动或混合模式，按下一节设置「稳定」组。
4. 启用导入的配置，将全局路由设为「配置」。手动或混合模式还需在首页选择普通代理流量的出口。

## 策略组

| 分组 | 行为 | 节点来源 |
| --- | --- | --- |
| 速度 | `fallback`：当前节点不可用时切换到组内其他可用节点 | 各地区筛选条件匹配的节点并集 |
| 稳定 | `fallback`：在指定节点之间进行故障切换 | 使用者自行指定，默认留空 |
| 地区组 | `url-test`：根据延迟测试自动选择节点 | 按节点名称中的地区关键词或旗帜匹配 |

地区组包括香港、台湾、日本、新加坡、韩国和美国。自动测试周期为 600 秒、超时为 5 秒；地区组使用 100 毫秒切换公差，减少小幅延迟波动造成的频繁切换。

「速度」是故障切换组，不保证选中延迟最低的节点。筛选条件只决定候选范围，节点顺序取决于客户端中的节点列表。名称不符合地区筛选条件的节点不会自动进入对应组。

### 设置稳定组

「稳定」默认使用不匹配任何节点的筛选条件 `(?!)`。请将它替换为自己选定节点的名称正则，并保留组类型 `fallback`。

例如，节点名称为 `Stable A` 和 `Stable B` 时，可使用：

```ini
policy-regex-filter=(?i)^(?:Stable A|Stable B)$
```

名称中的正则特殊字符需要转义，逗号可写为 `\x2c`。不要直接删除筛选条件。

**远程配置更新会覆盖本地编辑。** 在客户端自定义稳定组时，请另存本地副本，并删除或注释 `[General]` 中的 `update-url`。此后仍可更新远程规则集，但整份配置结构需要自行维护。需要保留自定义内容并更新整份配置时，可 fork 本仓库，修改生成规格后发布自己的配置。

## 更新与使用限制

- 每天北京时间 07:00 从上游重新构建并校验，通过后更新 `release` 分支中的配置；校验失败时保留上一版。
- 未修改的远程配置可通过各自的 `update-url` 更新。CDN 与客户端缓存可能导致更新延迟。
- 配置更新与节点订阅更新相互独立；导入配置不会添加节点。
- 自动测试用于判断探测目标的可达性，不保证所有网站、地区限制服务或长连接都可用。使用自动、混合模式前应确认稳定组已有可用节点。

## 本地构建

使用 Python 3，无需安装第三方依赖：

```sh
python3 -m unittest discover -s tests -v
python3 scripts/build.py --audience generic --out-dir dist/generic
```

构建会获取上游配置、应用 `src/overrides.py` 的覆盖规格，并检查分组引用、分流规则和远程规则集。生成文件位于 `dist/generic/`。

## 上游项目

- [johnshall/Shadowrocket-ADBlock-Rules-Forever](https://github.com/johnshall/Shadowrocket-ADBlock-Rules-Forever)：配置骨架。
- [blackmatrix7/ios_rule_script](https://github.com/blackmatrix7/ios_rule_script)：分类规则集。
- [iab0x00/ProxyRules](https://github.com/iab0x00/ProxyRules)：AI 服务规则集。
