# Karing 兼容性探针

这两个文件**不是可用配置**，只用来确认 Karing 认不认我们需要的几个 Clash 特性。
结论决定整个「适配 Karing」方案怎么建，所以先测再建。

## 怎么导入

Karing 的导入入口有三个（源码 `lib/screens/add_profile_by_*.dart`）：**从文件导入 / 从链接或内容导入 / 扫码导入**。
macOS 上从文件导入可能要授权目录，容易找不到——**建议走链接**：

底部「配置」→ 右上角 `+`（或「添加配置」）→ 选「从链接或内容」→ 粘贴下面任一 URL。

```
https://cdn.jsdelivr.net/gh/leon4z/shadowrocket-config@main/probe/1-rules.yaml
https://cdn.jsdelivr.net/gh/leon4z/shadowrocket-config@main/probe/2-groups.yaml
```

国内直连不通就用上面这两条（jsDelivr 国内可达）。两个可以先后导入，测完删掉即可。
（jsDelivr 对分支引用有缓存；我改了这两个文件的话会顺手清一次缓存。）

## 探针 1：规则集与规则类型

**测什么**：`rule-providers`（远程规则集）、`DOMAIN-WILDCARD`（Clash.Meta 才有的类型）。分组只用 `DIRECT`，不依赖节点，所以它跟节点无关，应该能独立跑通。

**期望**：配置能加载并启用；「分流规则检测 / 测试规则」里输入 `iphone.com` 命中 `PROBE`，输入 `www.apple.com` 也命中 `PROBE`。

**如果失败**：把 `rules:` 里 `DOMAIN-WILDCARD` 那行注释掉再导入一次。还能用 → 是 `DOMAIN-WILDCARD` 不支持；还是不行 → 是 `rule-providers` 不支持。

## 探针 2：分组能不能拿到节点

**这是整个方案的成败点。** 小火箭那份配置能公开托管，是因为它 `[Proxy]` 段为空、节点由 app 自己的订阅管理。Clash 配置里节点来自 `proxies:` 或 `proxy-providers:`，后者要写你的**节点订阅 URL**（付费凭据），不能进公开仓库。

所以关键问题是：**Karing 能不能把自己已订阅的节点，喂给配置里定义的分组？**

**期望**：打开「代理分组」，看 `PROBE-include-all-proxies` 和 `PROBE-include-all` 这两个组里有没有出现节点（应该有 🇸🇬 新加坡 / 🇲🇾 马来西亚 那一批）。两个组分别用 `include-all-proxies: true` 和 `include-all: true` 两种写法，请分别告诉我哪个有节点、哪个是空的。

**已知的负面线索**：Karing 自己带的 Clash 示例配置（`README_examples/clash/config.yaml`）里**没有**这两个参数。它示范的正则筛选用法是这种形式——需要指向一个 provider：

```yaml
- name: UseProvider
  type: select
  filter: "HK|TW"        # 正则，过滤 provider1 中节点名包含 HK 或 TW
  use:
    - provider1
```

而 `provider1` 是 `proxy-providers:` 里的条目，需要订阅 URL。

**如果两个组都是空的**：先别急着换方案，再去配置页看看**「配置合并」**（源码里有 `my_profiles_merge_screen.dart`）能不能把这份配置和你已有的节点订阅合起来——Karing 的原生模型本来就是「一套路由规则 + 多个订阅源」。

## 测完告诉我什么

1. 探针 1：能不能加载？`iphone.com` 命中了吗？（失败的话，注释掉 `DOMAIN-WILDCARD` 后的结果）
2. 探针 2：两个组里哪个有节点，哪个空？
3. 「配置合并」有没有这个入口？
