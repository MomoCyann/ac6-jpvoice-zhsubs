# Agent 交接文档

> **给接手这个项目的 AI Agent（或人类开发者）**。
> 这份文档假定你要继续修改代码或排查问题，不是来玩游戏的。
> 先读这一份，再按需读 [FORMAT](FORMAT.md) 和 [WORKLOG](WORKLOG.md)。

---

## 1. 你在接手什么

一个**资源补丁工具**，用于把《Ace Combat 6》汉化版的中文资源迁移到日文语言槽，实现"日文语音 + 中文字幕"。

- 运行平台：Windows，Python 3.9+，依赖 `numpy`
- 处理对象：`assets/DATA.TBL` + `DATA00.PAC`（2.44 GB）+ `DATA01.PAC`（686 MB）
- 写入策略：**只追加，不改原字节**。所有改动通过改 `DATA.TBL` 的索引生效

代码规模很小（约 600 行），但**每一行都对应一个踩过的坑**。改动前读完 §5「不变量」。

---

## 2. 文件职责

| 文件 | 职责 | 不要在这里做什么 |
|---|---|---|
| `scripts/ac6lib.py` | 底层：π 垫、压缩/解压、FHM 解析、TBL 读写、`Game` 门面 | 不要加业务逻辑（"哪个包要改"） |
| `scripts/apply.py` | 业务：`task` / `cg` / `dup` / `all` 四个模式 | 不要在这里实现新的格式解析 |
| `scripts/audit.py` | 验证：槽一致性 + 原版基线比对 | 不要在这里写文件（它是只读的） |

**修改原则**：格式相关的改动进 `ac6lib.py`，条目选择相关的改动进 `apply.py`。

---

## 3. 当前状态（精确快照）

### 3.1 已完成的改动

| 阶段 | 条目 | 操作 | 状态 |
|---|---|---|---|
| `task` | `#9` ~ `#23`（15 个） | `chunk4 ← chunk5`（字体）；语言容器 `slot0 ← slot1` | ✅ 已写入，玩家实测：菜单 / 简报 / 无线电 / 任务内过场全部中文 |
| `cg` | `#474,477,479,482,485,487,489,493,496,498,501,503,505,507,510,515,521,529`（18 个） | 语言容器 `slot0 ← slot1` | ⚠️ 已写入，但**实测无效**：CG 字幕仍是日文，改的是字体不是字幕 |
| `dup` | `#103←#9, #109←#15, #111←#17, #115←#21, #117←#23` | 从正本取 `slot1`，写入副本 `slot0` 和 `slot1` | ✅ 已写入，玩家实测任务一过场正常 |

### 3.2 验证结果

```
language containers: 42, slot0==slot1: 42, mismatched: 0
font packages (CAPT present but layout table empty): 6 -> [467..472]
slots still identical to stock Japanese: 0
VERDICT: PASS
```

### 3.3 用户环境实测结果（玩家实机，非推测）

| 场景 | 结果 |
|---|---|
| 菜单 / UI | ✅ 中文 |
| 任务简报（briefing）字幕 | ✅ 中文 |
| 无线电字幕（radio） | ✅ 中文 |
| 任务内实时过场（`dd01` / `dd07` / `dd09` / `dd13` / `dd15`） | ✅ 中文（第一关验证） |
| 章节 CG（`ed00` ~ `ed15`，含开场动画） | ❌ **确认仍是日文**（改动前后画面无任何变化） |

> 验收深度：玩家实测覆盖到**任务一**及其前后衔接。其余关卡未逐关走查，但资源批次与脚本逻辑完全一致。
> **不要**再花时间在 `#474`~`#529` 上做槽位复制——已经试过，实测零效果。

---

## 4. 核心未解问题（你的主要任务）

### 4.1 症状

章节 CG（尤其开场 `ed00_01`）字幕显示日文。18 个 CG 包的槽位统一**已执行并验证写入正确**，但画面字幕**没有任何变化**——这条路径已被实测判定为死路。

### 4.2 已经排除的假设

| 假设 | 证据 |
|---|---|
| 视频内嵌字幕 | `moviepack.bin` 的 ASF 对象里 `Script`/`Caption`/`Subtitle` 出现次数全为 0 |
| 汉化组替换了视频 | `moviepack.bin` 与原版 ISO **7 个采样点全部逐字节一致** |
| 字幕在 `demopack_*.bin` | 那是 XMA2 音频 |
| 字幕在 `bgmpack.bin` | 那是背景音乐 |
| `#474`~`#529` 是字幕包 | 全库对比显示**汉化组一个字节都没改这些包**；CAPT 排版表**全部为空**；且写入后实测无变化 |
| 写错 PAC 导致静默失效 | CG 包 `flags=0x01010000` → `DATA01.PAC`，偏移已校验，排除 |

### 4.3 当前最强假设

**CG 字幕可能压根没有被汉化组汉化**——`#474`~`#529` 只是字形容器，真正的 CG 字幕也许仍是原版日文资源，或者走了某个非资源层的路径（如视频叠加层 / 引擎硬编码）。

这意味着 `apply.py cg` 模式**对 CG 字幕无效**，保留只是为了资源一致性。

### 4.4 建议的排查顺序

1. **决定性实验（先做这个，一次就能定性）**：用「原版 ISO + 原版 assets」播放同一段开场 CG，与汉化版对比。
   - 两边都是日文 → 汉化组没做 CG 字幕，任务性质变成"从零制作 CG 字幕"，成本极高，应先与用户确认是否值得
   - 汉化版原本中文、打补丁后变日文 → 说明补丁覆盖了某处，需回查写入记录与备份时间线

2. **检查 `#878`~`#924`**：这批被汉化组改过的条目还没分析过。数量多（47 个）、体积小（4288 字节左右）、熵接近 8（加密或压缩）。有可能是字幕索引/配置。

3. **检查 `#500`、`#618`、`#683`、`#779`**：同样被改过，单个条目，用途未知。

4. **追踪运行时**：在重编译版里 hook 资源加载函数，记录播放 `ed01` 时实际读了哪些条目号。这是最直接的方法，但需要能编译 `rexglue`。

5. **检查游戏目录**是否有独立的字幕配置文件（`.srt` / `.txt` / `.ini`）。

### 4.5 排除方法（避免重复劳动）

- 不要再去解 NTXR 纹理找文字：DXT5 + Xbox360 tiled，缺一层 swizzle，目前解出来是噪声
- 不要再去扫明文：UTF-8 / GBK / UTF-16LE / UTF-16BE 全库 0 命中
- 不要动 `#467`~`#472`：那是字体包，重复处理会导致方块字

---

## 5. 不变量（改代码必须保持）

这些是**已验证的约束**，破坏任何一条都会导致崩溃、黑屏或静默失效。

### 5.1 压缩垫必须用十六进制 π

```python
i = idx % 256
pad = pack(">II", W[2*i + 1], W[2*i + 2])   # W = π 的十六进制小数的 32 位字数组
```

- `idx` 是**记录在 DATA.TBL 中的下标**，不是 PAC 偏移
- 必须是**十六进制**展开，十进制会全错
- `% 256` 是引擎的循环表大小，不能省

### 5.2 读取时两个 PAC 都试，用 `uncomp_size` 校验

```python
for pac in (DATA00, DATA01):
    ...
    if len(data) == uncomp_size:
        return data
```

按 flags 单读会造成大量错位（曾出现 797 条）。

### 5.3 写入时用 flags 判断归属

```python
name = "DATA01.PAC" if (flags & 0x01000000) else "DATA00.PAC"
```

CG 包（`#474`~`#529`）的 flags 是 `0x01010000`，属于 **DATA01**。写错文件 = 静默失效（因为索引指向另一个文件）。

### 5.4 追加式写入

`set_chunk()` / `append_raw()` **只能往后追加**，绝不能原地覆盖。
这保证"回滚 = 还原 DATA.TBL"，是整个方案可维护性的基础。

### 5.5 语言容器的判定条件

必须**三条同时满足**：

1. `nof` 在 2 ~ 12
2. 每个子块魔数都是 `FHM `
3. 每个子块的 **chunk0** 前 4 字节是 `CAPT`

只判断前两条会产生大量假阳性（顶层包有 26 / 55 个子块）。

### 5.6 幂等性

每个写操作前必须比较 `md5(当前槽) == md5(目标槽)`，相同则跳过。
否则每跑一次 PAC 就膨胀一轮。

### 5.7 以汉化包为基底

永远不要把"原版 ISO 解出来的包"作为重建基底 —— 会丢掉汉化组的字体/排版改动。

---

## 6. 脚本契约

### 6.1 `ac6lib.Game`

```python
g = Game(assets_dir)                  # 只读打开两个 PAC（mmap）
g.tbl_rows                            # [(flags, off, comp, uncomp), ...]
g.extract(idx)                        # -> bytes | None（双 PAC 探测）
g.pac_for(idx)                        # -> "DATA00.PAC" | "DATA01.PAC"
g.store_blob(idx, blob, rows)         # 压缩 + 追加 + 更新内存行，不写 TBL
g.save_tbl(rows)                      # 落盘 DATA.TBL
g.refresh()                           # 重开 mmap（写完想读回来时用）
```

**注意**：`store_blob` 修改的是**传入的 rows 列表**，不是 `g.tbl_rows`。批量操作时要显式传同一个列表，最后统一 `save_tbl`。

### 6.2 FHM 工具

```python
fhm_chunks(d)          # -> (nof, offsets, sizes) | None
chunk(d, i)            # -> bytes，第 i 个子块
set_chunk(d, i, payload)  # 追加式替换，返回新 bytes
```

### 6.3 `apply.py` 模式

```
python apply.py task   # 只处理任务包（字体 + 语言容器）
python apply.py cg     # 只处理 CG 包
python apply.py dup    # 只处理副本包
python apply.py all    # 三个都做
python apply.py list   # 打印索引表，验证解析
```

参数：`--dir`（必需）、`--dry-run`、`--no-backup`。

### 6.4 `audit.py`

```
python audit.py --dir <assets> [--iso <iso> --iso-tbl <DATA.TBL.orig>] [-v]
```

退出码：`0` = PASS，`1` = REVIEW。

---

## 7. 禁忌清单

| ❌ 不要 | 原因 |
|---|---|
| 原地覆盖字节 | 失去回滚能力 |
| 用十进制 π | 全部解压失败 |
| 按 flags 单 PAC 读取 | 大量错位 |
| 只处理正本不处理副本 | `#103/109/111/115/117` 会残留日文 |
| 只写 `slot0` | 实测 `slot1` 会残留英文 |
| 动 `#467`~`#472` | 字体包，会出方块字 |
| 在 `audit.py` 里写文件 | 它必须是只读的 |
| 假设所有包 `nof == 4` | 会漏 `c22/c23` 等不同结构 |
| 用 `moviepack.bin` 找字幕 | 无字幕流，且与汉化无关 |

---

## 8. 如何验证你的改动

1. **单元级**：`python apply.py list --dir <assets> | head`
   → 能打印 `flags/off/comp/raw/pac` 就说明 TBL 与 PAC 对应关系正确

2. **变更级**：`python apply.py all --dir <assets> --dry-run`
   → 输出应该全是 `already ok` / `unchanged`（在已打过补丁的库上）
   → 如果有 `would update`，说明有槽位不一致，去看是哪几个

3. **整体级**：`python audit.py --dir <assets>`
   → 期望 `language containers: 42, slot0==slot1: 42, mismatched: 0`，`VERDICT: PASS`

4. **基线级**（需要原版 ISO）：`python audit.py --dir <assets> --iso <iso> --iso-tbl <orig.tbl>`
   → 期望 `slots still identical to stock Japanese: 0`

5. **游戏内**：这是唯一真正的验收标准。菜单 → 任务 1 开场 → 任务 1 结束 → 任务 7 → 任意 CG。

---

## 9. 环境备忘

- 目标游戏：`F:\Ace Combat 6 Fires of Liberation`
- assets：`F:\Ace Combat 6 Fires of Liberation\assets`
- 原版 ISO：`F:\ac6\Ace Combat 6 - Fires of Liberation (USA, Japan) (En,Ja).iso`
- 原版 TBL：`F:\ac6_probe\DATA.TBL.orig`
- 备份：`DATA.TBL.bak-jpzh-*`（本轮）、`DATA.TBL.bak-dupfix-*`、`bak-subswap-*`、`bak-langswap-*`

**Windows 注意**：Python 里写路径用 `F:/xxx` 或 `r"F:\xxx"`，不要写 `/f/xxx`（会被解释成 `C:\f\xxx`）。

---

## 10. 与用户沟通的建议

- 用户能实测、能提供"哪一关、哪一段、什么现象"这种关键定位信息 —— **多问，比盲猜快十倍**
- 用户明确表示过不想折腾翻译（成本太高），**不要主动提"改翻译"**
- 报告结论时给"可验证的下一步"，而不是"可能大概也许"
- 如果结论是"做不到"，直接说做不到并给证据，**不要用模糊表述拖时间**
