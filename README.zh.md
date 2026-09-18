# Voice Shell for Claude Code

[English](README.md) · [Русский](README.ru.md) · [Español](README.es.md) · **中文**

手机揣在兜里、屏幕黑着，用嘴跟一个已经跑起来的 Claude Code 会话对话，答案直接送进耳朵。

```
你
  ↕ 说话
Android 应用
  ↕ WebSocket（局域网 / Tailscale / HTTPS）
守护进程
  ↕
一个长期存活的 Claude Code 会话
  ↕
代码仓库 / shell / git / 测试
```

它不是又一个 agent。它是语音通道、意图分流，以及一层加在现成会话之上的表达层。

---

## 为什么不用现成的那些

Claude Code 本来就有语音模式：在键盘前按住空格，它把你说的话打成字。那是听写——人还在机器前，
眼睛还盯着屏幕。

Claude Code Remote Control 和 Cursor 的手机应用把会话搬到手机屏幕上。那是遥控器——你还是在看东西。

这里屏幕上什么都没有。唤醒词跑在手机本地，所以麦克风可以一直开着，音频却不会离开设备。
你说一声「Клод」，等一个提示音，然后讲你的事。Claude 在仓库里干活，再用一到三句话把结果念给你听。
它说到一半你可以打断。你也可以扔给它一件活儿转身就走——后台任务跑在各自的 git worktree 里，
等耳朵空下来再来汇报。

代价是适用面很窄。装之前先读[局限](#局限)。

---

## 需要什么

| | |
|---|---|
| 一台不关机的机器 | 一台 VPS，或者家里自己的电脑。守护进程只在 Linux 上跑过，别的都没验证。 |
| Claude 的访问权 | 自己给自己用，就用 Claude 订阅（Pro 或 Max）；否则用 Anthropic API key。见[用哪种凭据](#用哪种凭据)。 |
| 一部 Android 手机 | 应用只有 Android 版。没有 iOS 应用，iOS 排在路线图的最后一个阶段。 |
| 蓝牙耳机 | 不是必需，但整件事就是为它做的。应用会启用耳机自己的麦克风，免得手机隔着一层布听。 |

还有一个浏览器客户端，用来什么都不装先试一下。它弱一些：没有唤醒词，也没有随时可用的「停」。
见 [`docs/DEPLOY.md`](docs/DEPLOY.md)。

### 用哪种凭据

Claude Pro 或 Max 订阅适用于**你**在**自己的**机器上**给自己**跑这套东西。
自 2026 年 2 月起，Anthropic 不再允许在第三方产品中使用订阅的 OAuth 凭据，
因此你不能把它架成一个服务、让别人用你的订阅来说话。
只要有你之外的人要用某个实例，这个实例就得配自己的 Anthropic API key。

引出这个问题的缘由，以及为此写的那封信，都在
[`docs/anthropic-inquiry.md`](docs/anthropic-inquiry.md)。

---

## 安装

### 服务器

通过 ssh，手边只有手机也行：

```bash
# 先看一眼，什么都不改
curl -fsSL https://raw.githubusercontent.com/aisarus/Ccvoice-/claude/voice-shell-claude-code-77wwh2/scripts/install-server.sh | CHECK=1 bash

# 再装
curl -fsSL https://raw.githubusercontent.com/aisarus/Ccvoice-/claude/voice-shell-claude-code-77wwh2/scripts/install-server.sh | sudo bash
```

脚本会装好 Node、Claude Code CLI 和 Python 依赖，跑一遍测试，写好 systemd 单元，
最后打印出地址和令牌。它卡在测试这一步：测试不过，服务就不会起来。

把域名指向这台服务器，它会用 Caddy 配上 HTTPS：

```bash
curl -fsSL https://raw.githubusercontent.com/aisarus/Ccvoice-/claude/voice-shell-claude-code-77wwh2/scripts/install-server.sh | sudo DOMAIN=voice.example.com bash
```

没有域名时端口是敞开的，流量走明文 HTTP。令牌能挡住外人，但不加密对话内容。
要么配域名，要么用 Tailscale 并用防火墙关掉端口。

之后服务器靠两条命令过日子：

```bash
sudo bash /opt/voice-shell/scripts/update-server.sh   # fetch + reset，然后重启
sudo bash /opt/voice-shell/scripts/doctor.sh --fast   # 一份涵盖全部的报告
```

更新不是 `git pull`。分支分叉时 `pull` 会停下来问你怎么合并；这里没什么可合的，分支就是准绳。
`update-server.sh` 做的是 fetch + reset，会先打印哪些东西将要消失，并留下一个
`before-update-…` 标签供你退回。

### 跑在自己电脑上的守护进程

云上的机器看不见你的电脑。想让 Claude Code 动真正的项目，守护进程就得跑在项目所在的地方：

```bash
curl -fsSL https://raw.githubusercontent.com/aisarus/Ccvoice-/claude/voice-shell-claude-code-77wwh2/scripts/install-desktop.sh | bash -s -- ~/你的项目
```

它会克隆仓库、装依赖、跑测试、生成令牌并启动守护进程。出门在外就走 Tailscale。

如果那台电脑前面正好坐着一个 Claude Code 会话，把
[`docs/desktop-handoff.json`](docs/desktop-handoff.json) 交给它：同样的步骤，
机器可读，连该检查什么、该怎么回话都写好了。

### 应用

[下载 APK](https://github.com/aisarus/Ccvoice-/releases/download/apk-latest/app-debug.apk)
——每次推送到工作分支都会重新构建。授予麦克风和通知权限：没有通知权限，
Android 根本不允许前台服务运行。然后填三个字段，一次就够：地址、令牌、初始语言。

> **发布的 APK 是用一把调试密钥签名的，而这把密钥连同密码就放在本仓库里**
> ——keystore 是 `android/app/voice-shell.keystore`，密码明文写在
> `android/app/build.gradle.kts` 里。这是故意的：否则 CI 每次构建都会换一把随机密钥，
> 新版就装不到旧版上面去。后果是实打实的：任何人都能构建出用同一把密钥签名的 APK，
> 而 Android 会把它当成你那个应用的更新装上去。**APK 只从本仓库的 release 页面下载安装。**
> 自己构建的话，请生成自己的 keystore，并且不要提交进仓库。

---

## 怎么说话

分两拍。大多数人栽在这一步：

> 「Клод」· 停一下 · 等提示音 · 「跑一下测试」

唤醒词由手机上的一个小模型负责（Vosk，约 45 MB，首次启动时下载）。
它只管两件事：「Клод」和「停」。正式那句话交给手机自带的识别器，准确得多，但不是立刻就绪。
一口气说完也能用，只是差些：3.5 秒后应用不再等待，直接把小模型听到的内容发出去。

「Клод」之后的提示音是准你开口的信号。不等它就说，半句话会丢掉。

| 声音 | 含义 |
|---|---|
| 短促「嘀」加震动 | 麦克风已开，说吧 |
| 轻轻一声「咔」加短震动 | 这句已收下并发出 |
| 低沉一声 | 什么都没听清，麦克风白开了一次 |

唤醒词允许差一个字母：「клот」「клад」「плод」都算数。
「Код」「чат」「кот」「что」「как」永远不算——这些是命令的开头。
在它回话途中喊唤醒词就是打断：Claude 立刻闭嘴开始听。

每句话都会落到三个目标之一，你不点名时由守护进程自己判断：

| 目标 | 是什么 | 会改动项目 |
|---|---|---|
| `код` | 工作目录里的 Claude Code | 会 |
| `чат` | 另起一段对话，不接触项目；只有网页搜索和读取网页 | 不会 |
| `заметка` | 往收件箱文件里追加一行 | 不会 |

分不清时一律走 `чат`，这是有意为之：走错 `чат` 顶多白说一句，走错 `код` 已经动过手了。

撤销、记忆、后台任务、Telegram 通道和语音确认，这些 shell 自己就处理了，不惊动 Claude。
它认识的全部说法在 [`docs/VOICE.md`](docs/VOICE.md)。

### 命令短语全是俄语

这一条比本页其他任何内容都重要。唤醒词「Клод」和「claude」都应；
正式那句话可以用俄语、英语或希伯来语识别，Claude 也会用你说的语言回答。
但 **shell 自己**认识的那些短语——分流前缀、撤销、记忆、后台任务、Telegram 通道、
权限提问的是与否——只有俄语版本。英语里接好的只有
「stop」「quiet」「enough」「shut up」「stop working」「abort」「cancel」和切换语言。

中文一句也没有。用中文说话时，这东西就是一条通往 Claude Code 的语音管道，再没别的：
其他语言的短语表还没人写。

---

## 可选的部件

**GitHub。** `код` 这个目标跑在真正的 shell 上，所以只要有令牌，GitHub 就能通过 `gh` 用起来：

```bash
sudo bash /opt/voice-shell/scripts/setup-github.sh ghp_你的令牌 "你的名字" you@example.com
```

需要 root，也需要服务已经起来：脚本会写 `/etc/voice-shell.env`。
令牌在 [github.com/settings/tokens](https://github.com/settings/tokens) →
classic → 勾 `repo` 和 `workflow`。令牌不合格会当场被拒，而不是留作日后的意外。

**Telegram。** 只发往一个事先设定的聊天，这样文件能靠语音送出机器，
旁人的嗓音却改不了收件人：

```bash
sudo bash /opt/voice-shell/scripts/setup-telegram.sh
```

它会问你从 @BotFather 拿到的 bot 令牌，再从你发给 bot 的第一条消息里认出聊天 id，
然后往那里发一条测试消息。

**语音确认。** 默认关闭：`PERMISSION_MODE` 是 `auto`，什么都不问。
`guarded` 在破坏性操作前发问，`ask` 除安全的读取之外一律发问。

---

## 在本地跑

```bash
pip3 install -r daemon/requirements-dev.txt
cd daemon && python3 -m voice_claude --workspace ~/你的项目
```

客户端和 WebSocket 共用一个端口（`8787` 或 `$PORT`），守护进程启动时会打印令牌。
走明文 `http://` 时，浏览器只把麦克风给 `localhost`。

没有接上凭据时，`code` 和 `chat` 会返回占位答复。整条链路照样跑通，
你听到的是一句老实话「Claude 不可用：……」并附上原因，而不是编出来的答案。

```bash
python3 -m pytest tests -q        # 250 passed
python3 scripts/validate_spec.py  # OK: voice-shell-for-claude-code v0.2.0 (29 top-level sections)
```

校验器不带依赖也能跑，那时只检查交叉引用；装上 `pip install jsonschema` 才会按 schema 校验。

---

## 局限

**没做的：**

- **设备端的 VAD 断句。** 应用靠计时器判断你说完了，不是真的听出你停了。
- **声纹。** 说话人分类器里有一个 `with_voiceprint` 配置和对应权重，
  但应用从不计算声纹相似度，这套配置因此从未真正生效。角色只靠声学特征判断。
- **多项目。** 一个守护进程只对应一个工作目录，不能用语音切换会话。
- **服务端语音识别。** 识别发生在手机上，浏览器客户端里则发生在 Google。
  守护进程自始至终看不到音频。
- **ambient 模式下的主动提示。** `passive` 是好用的：缓冲区留十分钟，也能回答关于它的问题。
  `assist` 的触发条件写在规格里，但从未被触发过。
- **iOS。** 没有。

**做了但没调过的：**

- **说话人识别**已经实现，也有针对合成配置的测试覆盖。但它从未用真实录音标定过，
  所以那些阈值都是猜的。刚启动时，你自己压低嗓音说的话八成会被判成 `unknown`。

**扎手的地方：**

- 「Откати」单独说出来就是撤销命令。shell 命令只在句首匹配，
  前面允许「клод」「давай」「ну」这类语气词，所以谈论撤销本身是安全的——
  但一句以「откати」开头的话会真的执行 `git reset --hard` 退回上一个检查点。
  被撤掉的那个 commit 仍留在 git 历史里。
- 后台任务要求工作目录是一个 git 仓库。同时最多跑两个。
- Telegram 拒发 `.env`、各类密钥、keystore，以及名字里含 token/secret/password 的文件，
  也拒发工作目录之外的文件和超过 45 MB 的文件。它会出声拒绝，并说明原因。
- systemd 下的守护进程通常以 root 运行。Claude CLI 拒绝在关闭权限检查的情况下以 root 启动，
  所以那套行为改由回调提供。
- 检查点是按「一句话」写的，不是按文件：撤销会把这一句话改动的东西全部收回。

---

## 仓库里有什么

| 路径 | 是什么 |
|---|---|
| [`spec/voice-shell.json`](spec/voice-shell.json) | 完整规格，机器可读——唯一的事实来源 |
| [`spec/voice-shell.schema.json`](spec/voice-shell.schema.json) | 规格的 JSON Schema（draft 2020-12） |
| [`scripts/validate_spec.py`](scripts/validate_spec.py) | 校验器：schema 校验加交叉引用一致性检查 |
| [`daemon/`](daemon/) | `voice-claude-daemon`：说话人角色、分流、语音措辞、WebSocket |
| [`android/`](android/) | 应用：本地唤醒词、后台服务、耳机按键 |
| [`client/web/`](client/web/index.html) | Android 上的 Chrome 客户端：按住说话、STT、TTS、提示音 |
| [`tests/`](tests/) | 250 个测试，含走真实协议的端到端用例 |
| [`Dockerfile`](Dockerfile) · [`render.yaml`](render.yaml) | 一键部署，用手机就能做 |
| [`docs/VOICE.md`](docs/VOICE.md) | 系统自己能听懂的全部说法 |
| [`docs/TROUBLESHOOTING.md`](docs/TROUBLESHOOTING.md) | 症状 → 查什么 → 怎么修 |
| [`docs/DEPLOY.md`](docs/DEPLOY.md) | 不用电脑的分步部署 |
| [`docs/desktop-handoff.json`](docs/desktop-handoff.json) | 给你 PC 上那个 Claude Code 会话的机器可读任务书 |

---

## 关键取舍

- **物理动作只有一个：`ACTIVATE`。** 不搞单击/双击/滑动那一套词汇表——
  那会把它变成电视遥控器。其余全部交给语音和上下文。
- **耳机按键是备用通道。** 期望的比例：约 90% 靠唤醒词，约 9% 靠对话窗口自然延续，约 1% 靠按键。
- **15 秒的对话窗口。** 它答完之后，不必再喊唤醒词。
- **你说的话从不被改写**，Claude 的输出则被压成一到三句；完整输出留在屏幕上。
- **打断分成两种。**「Стоп」只停住声音；「останови работу」才是中断 Claude Code。
- **按意思分流，不按暗号分流。** 词表瞬间解决显而易见的情况；
  面对真实口语，则由一个快模型在 2.5 秒内选出目标，选不出就算了。
  说出口的前缀、界面上的选片、上一次的纠正，都已经是人的决定，不再拿去追问。
- **一个长期存活的会话**，而不是每次请求都新起一个 Claude。
- **不自建云。** 在家走局域网，出门走 Tailscale。

### 是谁在说话

应用给每一段语音判定一个角色，并作为一行前置的服务信息交给 Claude：

```
[voice-shell] speaker=master (говорит мастер) confidence=0.93 device=phone_mic

修掉这个 bug，然后跑测试。
```

音量是**主要**信号，但不是唯一信号：旁边有人凑近说话，或者你自己小声嘀咕一句，
都会让只看音量的分类器失灵。真正的判定是一个加权逻辑回归，输入包括相对音量、信噪比、
直达声与混响声之比、C50、高频成分、近讲效应、可选的本地声纹，
以及设备和对话连续性带来的先验。

| 角色 | 含义 | 允许做什么 |
|---|---|---|
| `master` | 机主近距离、清晰的说话 | 全部：唤醒、下令、打断、确认 |
| `bystander` | 别人说话、电视、隔壁房间 | 什么都不行；默认连发都不发 |
| `unknown` | 把握不够 | 什么都不执行，只反问一次 |
| `self_echo` | 麦克风里收到的自家 TTS | 直接丢弃 |

把握不够时角色落到 `unknown`，而不是硬猜一个。
确认只接受来自 `master` 且置信度 ≥ 0.85 的回答。

### 第二只耳朵（ambient）

默认关闭。三个子模式：`off`；`passive`（本地十分钟环形转写，你不问它就不出设备）；
`assist`（转写送往 chat 目标）。

原始音频从不保存，退出该模式时缓冲区清空，别人的话未经单独授权不会进入缓冲区。
录别人的声音在不同法域下规定不同——所以这是一个默认最保守的开关，而不是实现细节。

ambient 模式下的回答是 `whisper_output`：一句话，最多十二个词，音量 −6 dB，
只在 1.2 秒以上的静默间隙里说，绝不压着你的话说。
ambient 下唤醒词是关掉的——当着别人的面喊「Клод」，正是这个模式要避免的事。

---

## 其他语言

[English](README.md) · [Русский](README.ru.md) · [Español](README.es.md) · **中文**
