"""覆盖规格：从上游 lazy_group.conf 生成配置时要做的全部改动。

这个文件是仓库里唯一需要手工维护的东西。build.py 每天拉一次上游的
lazy_group.conf，按这里的规则改，产出两份配置发布到 release 分支。

为什么不是直接存一份完整配置：上游对配置结构的改进（新增服务、调整规则
顺序、更新注释）会持续发生。存拷贝的话这些改进永远进不来，只有规则集内容
会跟着上游变。存规格的话，上游改了什么都能自动跟进来；万一把这里依赖的
锚点改掉了，构建会显式失败，而不是静默产出一份错的配置。

规格里的每一项都对应 README「与上游的差异」一节。
"""

# --------------------------------------------------------------------------- #
# 上游
# --------------------------------------------------------------------------- #

UPSTREAM_URL = (
    "https://raw.githubusercontent.com/johnshall/"
    "Shadowrocket-ADBlock-Rules-Forever/release/lazy_group.conf"
)

# 上游的锚点：这些必须存在于抓到的内容里，否则说明上游改了结构，
# 下面所有基于模式的替换都可能失配 —— 直接让构建失败。
ANCHORS = [
    "FINAL,PROXY",
    "rule/QuantumultX/Apple/Apple.list",
    "rule/QuantumultX/WeChat/WeChat.list",
    "rule/QuantumultX/Global/Global.list",
    "rule/QuantumultX/China/China.list",
    # 上游所有服务组（select 类型、指向 PROXY）。上游新增服务组时会多出新的，
    # 那些会被 PROXY→速度 的通用替换自动接住，不需要改这里。
    "AI = select,PROXY",
    "YouTube = select,PROXY",
    "Netflix = select,PROXY",
    "Disney+ = select,PROXY",
    "Max = select,PROXY",
    "TikTok = select,PROXY",
    "Spotify = select,DIRECT,PROXY",
    "Telegram = select,PROXY",
    "Twitter = select,PROXY",
    "Facebook = select,PROXY",
    "PayPal = select,DIRECT,PROXY",
    "Amazon = select,DIRECT,PROXY",
    "苹果服务 = select,DIRECT,PROXY",
    "谷歌服务 = select,PROXY",
    "微软服务 = select,DIRECT,PROXY",
    "哔哩哔哩 = select,DIRECT,PROXY",
    "游戏平台 = select,DIRECT,PROXY",
]

# --------------------------------------------------------------------------- #
# 1. 规则集方言：4 处圈X 引用 → 小火箭版
# --------------------------------------------------------------------------- #
# 上游这四个分类引用的是 rule/QuantumultX/ 下的文件，里面的 IP 规则不带
# no-resolve。小火箭遇到不带 no-resolve 的 IP 规则，会为了判断域名是否命中
# 而发起本地 DNS 查询。两版的 IP 规则集合逐条相同，唯一差别就是小火箭版每条
# 都带 no-resolve。
#
# 注意小火箭版把域名拆到了 X_Domain.list（裸域名，带前导点），必须用
# DOMAIN-SET 引用 —— 只换 RULE-SET 那一行会丢掉全部域名规则。
# 另外圈X 版里的 HOST-WILDCARD 规则在小火箭版没有对应写法，用小火箭原生的
# DOMAIN-WILDCARD 显式补回来。

RULE_BASE = "https://raw.githubusercontent.com/blackmatrix7/ios_rule_script/master/rule"

RULESET_DIALECT = [
    {
        "qx": f"{RULE_BASE}/QuantumultX/Apple/Apple.list",
        "sr": f"{RULE_BASE}/Shadowrocket/Apple/Apple.list",
        "sr_domain_set": f"{RULE_BASE}/Shadowrocket/Apple/Apple_Domain.list",
        "wildcards": [
            "*-content.icloud.com.cn", "apple.*", "apple.com.*",
            "appleworldwidedeveloper.*.net", "imac.*",
            "init*.push-apple.com.akadns.net", "init*.push.apple.com",
            "ipad.*", "ipadair.*", "ipadmini.*", "iphone.*", "ipod.*",
            "macbook.*", "macbookair.*", "macbookpro.*",
        ],
    },
    {
        "qx": f"{RULE_BASE}/QuantumultX/WeChat/WeChat.list",
        "sr": f"{RULE_BASE}/Shadowrocket/WeChat/WeChat.list",
        "sr_domain_set": None,   # WeChat 没有 _Domain.list，只换那一行
        "wildcards": [],
    },
    {
        "qx": f"{RULE_BASE}/QuantumultX/Global/Global.list",
        "sr": f"{RULE_BASE}/Shadowrocket/Global/Global.list",
        "sr_domain_set": f"{RULE_BASE}/Shadowrocket/Global/Global_Domain.list",
        "wildcards": [],
    },
    {
        "qx": f"{RULE_BASE}/QuantumultX/China/China.list",
        "sr": f"{RULE_BASE}/Shadowrocket/China/China.list",
        "sr_domain_set": f"{RULE_BASE}/Shadowrocket/China/China_Domain.list",
        "wildcards": ["windows-*.net"],
    },
]

# --------------------------------------------------------------------------- #
# 1b. [General] 段的覆盖
# --------------------------------------------------------------------------- #
# 把上游 [General] 里的某个键改成指定值。上游没有这个键时追加到段末，
# 所以不依赖上游一定保留它。
#
# ipv6：启用 IPv6 支持。上游是 true（同时查 A 和 AAAA 记录）。改成 false
# 关闭 IPv6 解析，避免 IPv6 出口不通时走 IPv6 导致连接卡住或分流异常。
#
# 注意手册的补充：即使这里设为 false，「当本地网络环境支持 IPv6，并且节点域名
# 支持 IPv6 解析，Shadowrocket 也会使用节点的 IPv6 地址进行访问」。要彻底避免，
# 需要关闭节点域名的 IPv6 解析，或在 [Host] 段给节点域名指定 IPv4 地址。
GENERAL_OVERRIDES = {
    "ipv6": "false",
}

# --------------------------------------------------------------------------- #
# 2. 分组调参
# --------------------------------------------------------------------------- #

# 所有组的 tolerance 统一改成这个值（上游是 0 混着的）。
# tolerance 的含义：只有当新优胜者的延迟比旧优胜者低出这么多毫秒，才切换节点。
#
# 只作用于会「择优」的类型（url-test）：上游那 6 个国家组是 url-test，会改成 100。
# select 类型不测速、fallback 类型按可用性切换，两者都不涉及 tolerance，
# 所以这里只替换已有的 tolerance，不给它们新增。
TOLERANCE = 100

# 从分组行里删掉的参数。policy-select-name 是「按名字指定默认选中项」，
# 上游用的是位置默认（select=0 表示成员列表里的第 1 个）。统一回原版逻辑，
# 避免出现「名字写错但看不出来」的情况。
DROP_GROUP_PARAMS = ("policy-select-name",)

# --------------------------------------------------------------------------- #
# 2b. 规则集的分发通道
# --------------------------------------------------------------------------- #
# 生成时把规则集 URL 从 raw.githubusercontent.com 改写成 jsDelivr。
#
# 为什么：小火箭在设备上要拉全部 37 个规则集。全指向 raw.githubusercontent.com
# 时实测（本机、网络通畅）总耗时 93.8s，其中两个 DOMAIN-SET 直接 30s 超时——
# raw 对短时间大量请求会限流，国内环境更差。改走 jsDelivr 后同样的 37 个文件
# 26.6s、0 失败。用户在 iPhone 上更新订阅时遇到的「超时」就是这个问题：
# 这份配置新引入了 7 个设备上没有缓存的规则集（4 个小火箭版 + 3 个 DOMAIN-SET），
# 必须现拉，正好打在 raw 最不稳的地方。
#
# 改写成通用规则（不是逐个列白名单），所以上游以后引用新的 raw 仓库也能自动接住；
# 万一某个仓库 jsDelivr 不服务，构建时的远程校验会失败并点名是哪个。
#
# 想换回 raw 就把它设成 False。代价是设备端拉取会明显变慢且可能超时。
USE_JSDELIVR_FOR_RULESETS = True

_RAW_BASE = "https://raw.githubusercontent.com/"
_JSD_BASE = "https://cdn.jsdelivr.net/gh/"

# --------------------------------------------------------------------------- #
# 3. 自建组（只用于 custom 变体）
# --------------------------------------------------------------------------- #
# 把上游指向内置 PROXY 的地方改指向这两个组。底层节点由正则从订阅里筛，
# 订阅换代不用改配置。
#
# 两个组都是 fallback：节点不可用时才切换到下一个可用节点，不追最快。
# 这里不要配 tolerance —— 手册里 tolerance 是「只有当新优胜者的分数高于
# 旧优胜者加公差时才换线」，「优胜者」是 url-test 择优的概念；fallback 是按
# 可用性切换，没有择优比较，配了也是无效参数。
#
# 注意 fallback 取的是「筛出来的第一个可用节点」，所以成员顺序即优先级。
# 用正则筛选时，顺序是订阅里节点的排列顺序，不是人工指定的顺序。
#
# 正则都拿本机 115 个真实节点名验证过命中集合：速度 命中 14 个（含另一个
# 订阅的 4 个 🇸🇬Singapore 0N），稳定 精确命中原来的 3 个。

EXTRA_GROUPS = [
    "速度 = fallback,"
    "policy-regex-filter=🇸🇬|SG|Singapore|新加坡|狮城|沪新|京新|深新|杭新|广新"
    "|🇲🇾|Malaysia|马来|马来西亚,"
    "interval=600,timeout=3,"
    "url=http://www.gstatic.com/generate_204",

    "稳定 = fallback,"
    "policy-regex-filter=Grande|GRANDE|BZ-VMess|BZ-VMESS,"
    "interval=600,timeout=5,"
    "url=http://www.gstatic.com/generate_204",
]

# 默认把所有 PROXY 换成 速度；这些分组例外，换成 稳定。
# （AI 和谷歌服务走美国故障转移组，其余走「最快东南亚」组。）
PROXY_TARGET_DEFAULT = "速度"
PROXY_TARGET_PER_GROUP = {
    "AI": "稳定",
    "谷歌服务": "稳定",
}

# --------------------------------------------------------------------------- #
# 变体
# --------------------------------------------------------------------------- #
# 两份配置的差别是「出口怎么定」，文件名按这个机制命名：
#   select   —— 服务组是 select 类型，指向内置 PROXY，出口由你在首页手动选
#   fallback —— 服务组指向自建的 速度/稳定 组，两者都是 fallback，自动故障转移

VARIANTS = [
    {
        "id": "Shadowrocket-select",
        "title": "上游原版逻辑 · 出口在首页手动选（PROXY）",
        "substitute_proxy": False,
    },
    {
        "id": "Shadowrocket-fallback",
        "title": "自建速度/稳定组 · 自动故障转移（fallback）",
        "substitute_proxy": True,
    },
]
