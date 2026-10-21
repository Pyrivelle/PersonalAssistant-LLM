# ZhuangFY 语音对话原型

这里独立练习语音对话，不依赖 Sakura 的桌面程序或插件市场。语音在本机完成 VAD 与 SenseVoice 识别；开启自动回复后，只有唤醒后的识别文字会发给配置的模型服务，麦克风音频不会上传给该对话接口。

## 从源码首次运行（Windows）

建议安装 Python 3.12，进入本目录后运行：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe download_models.py --sample
.\.venv\Scripts\python.exe dashboard.py
```

模型权重约 239 MB，下载脚本还会获取词表、VAD 模型和可选示例音频；下载后进行 SHA-256 校验，已校验的文件会复用。资源放在 `models/` 和 `samples/`，不纳入 Git。若无需“试用示例”按钮，可省略 `--sample`。VS Code 解释器选 `.venv\Scripts\python.exe`。`启动界面.cmd` 和 `启动LLM测试.cmd` 在完成上述环境安装后可直接双击使用。

## 独立 LLM 文字对话测试

双击 `启动LLM测试.cmd`，或在统一控制台打开“模型与人格”。窗口可选“硅基流动”“DeepSeek 官方”“Kimi 官方（中国站/国际站）”“哈基米 API”或“自定义兼容接口”，填写模型 ID、角色提示词，并在密码输入框填入 API Key。点击“获取模型列表”会在后台调用当前供应商的 `GET /models`，把返回的模型 ID 放进可搜索的下拉框；该列表可能包含不支持文字对话的模型，也可手动输入。获取列表不发送对话，不自动保存密钥。点击保存后，供应商、模型、提示词保存在 `llm_settings.json`；各供应商的密钥分别保存在本机 `.env.local`，界面只显示“已保存”，不会回显密钥。两个文件都已加入 `.gitignore`。无需另建文件，也不要把密钥发到聊天里。

硅基流动使用官方 `https://api.siliconflow.cn/v1/chat/completions`；原有默认模型 ID 为 `deepseek-ai/DeepSeek-V4-Flash`。DeepSeek 官方使用 `https://api.deepseek.com/chat/completions`，切换时初始模型为 `deepseek-flash`，也可选 `deepseek-v4-pro`。Kimi 中国站与国际站分别使用 `https://api.moonshot.cn/v1` 和 `https://api.moonshot.ai/v1`，Key 不互通；初始示例模型为 `kimi-k2.6`。哈基米 API 已按你确认的 `idc.hilxyo.cn` 设为 `https://api.hilxyo.cn/v1`，初始示例模型选自其当前公开目录的 `DeepSeek-V4-Flash`。未填密钥时，“获取模型列表”读取该站公开模型广场的 `/api/pricing`；填入密钥后改用账号的 `/v1/models`，实际可调用权限以账号为准。服务商可能调整可用模型，请以账户的实时模型列表为准，可在界面直接改成有权限使用的 ID。自定义接口按同样的 chat/completions 协议工作；远程地址须为 HTTPS，本机 Ollama 一类服务可使用 `http://localhost:11434/v1`。自定义本地服务可不填密钥。

输入一句文字并点击“发送”即可验证连接。程序发送角色提示词、最近六轮消息和当前输入；本轮成功后才加入对话历史，点击“清空当前对话”可重置。历史当前只保存在该窗口的内存中，关闭即清空。这个窗口继续保留为独立的文字调试入口；主语音窗口也会读取这里保存的模型设置。`llm.py` 负责配置与 HTTP 请求，`chat_worker.py` 负责后台请求，`llm_gui.py` 负责文字测试界面。

## 短期上下文与长期记忆

最近六轮对话仍作为短期上下文放在窗口内存中。长期记忆使用本地 [Mem0 开源库](https://docs.mem0.ai/open-source/overview)与 Qdrant 向量库；记忆数据在 `memory_data/`，已被 Git 忽略。语义检索使用硅基流动的 `BAAI/bge-m3` Embedding，记忆提取使用硅基流动的对话模型。相关记忆在每次模型请求前检索并作为参考数据加入上下文；成功回复后，程序在后台提炼用户明确说过的偏好、身份线索、项目决定或经历。提炼不是逐字保存全部聊天，助手推测和未执行的行动不应当当作事实。

**默认关闭长期记忆。**在“模型设置与文字测试”中勾选“启用长期记忆”并保存，才会开始把相关对话文字发送给硅基流动做记忆提取与向量化。这会产生额外 API 请求及可能的费用；本地数据库未加密，使用这台电脑的人可能读取它。Mem0 的遥测已在程序中关闭。若当前对话供应商改为自定义服务，长期记忆仍使用已保存的硅基流动密钥；没有该密钥时记忆请求会失败，但普通对话仍可继续。

主窗口或文字测试窗口的“查看与管理长期记忆…”可以浏览、手动添加、更正、删除单条记忆，或“遗忘全部”。手动新增/更正也会发送这条文字生成 Embedding；删除只处理本地记录。“遗忘全部”会在确认后删除整个本地记忆目录，包括向量、历史与内部记录。`清空当前对话`只清除短期上下文，不会删除长期记忆。对话提炼后会清除 Mem0 暂存的原始消息；单条删除也清除该记忆的本地历史记录。

启用后可说或输入“记住我喜欢简短解释”，记忆写入成功后才会答复已记住。说“忘记我喜欢简短解释”时，程序先找相似候选，再让模型严格挑选匹配项；找不到确定匹配就不会删除，请到管理窗口检查。明确说“更正”“现在用”“不再”等时，新记忆成功写入后会尝试识别并删除冲突的旧记忆；不确定时保留旧条目，需在管理窗口人工更正。自动提炼和冲突判断都可能出错，因此管理窗口是最终纠错入口。

建议先在文字窗口验收：开启长期记忆并保存；输入“记住我喜欢简短解释”；打开记忆管理确认条目；清空对话或重启窗口，询问“我喜欢怎样的解释”；然后输入“忘记我喜欢简短解释”，检查条目消失。真实云端 Embedding 与记忆提取需你启用后用自己的测试内容验证；本地自动化测试使用模拟服务，不上传个人资料。

## 庄方宜的人格设定

第一套正式人格包是 [`persona_packs/zhuang_fangyi_v1/prompt.md`](persona_packs/zhuang_fangyi_v1/prompt.md)，参考 [MengPaul07/zhuang-fangyi-skill](https://github.com/MengPaul07/zhuang-fangyi-skill) 的场景判断、语气和设定核验规则，改写为现实中的日常助手提示词。庄方宜也可以称作庄天师或小庄；她温和、理性、能处理复杂问题，也能自然地陪伴。用户不是默认的游戏角色，剧情里的共同经历不会冒充现实记忆。来源、适配边界和后续新增人格包的方法见 [`人格包说明`](persona_packs/zhuang_fangyi_v1/README.md)。

在控制台“模型与人格”或 `启动LLM测试.cmd` 中选择人格包，点击“载入人格包”会替换当前编辑框内容；再点击保存，语音窗口下一轮请求就会使用新设定，QQ 桥接重启后使用。直接编辑提示词也可以形成自定义版本。密钥输入框留空不会覆盖已保存密钥。旧版程序保存的内置默认词会自动升级为首套正式人格包；你自己写过的提示词保持原样。用户自定义内容只保存在本机 `llm_settings.json`，不纳入 Git。

建议用三轮文字对话验收：先问一个编程问题，再说“没理解，请用生活中的例子解释”，最后换成轻松闲聊。检查她能沿用前文主题、调整解释深度、避免重复开场。如果修改了提示词，最好清空当前对话再测，以免旧回复影响比较。人格一致性依赖模型输出，提示词无法保证每句话都完全符合设定。

调用可能产生服务商费用；只有点击“发送”才请求模型。“保存设置与密钥”不会发起模型请求。无真实密钥的本地验证可运行 `.\.venv\Scripts\python.exe -m unittest -v test_llm.py`。

本机已用硅基流动 `deepseek-ai/DeepSeek-V4-Flash` 完成一次真实请求，返回了简短中文问候。密钥没有出现在命令、测试输出或本说明中。

## QQ 消息桥接（SnowLuma）

SnowLuma 独立安装在 `C:\CProj\Airi\SnowLuma\v2.0.3`，不进入 ZhuangFY 仓库；可双击 `C:\CProj\Airi\SnowLuma\启动SnowLuma.cmd` 启动。它把 Windows QQ 转成 OneBot v11；`qq_bridge.py` 是 ZhuangFY 自己的客户端，用正向 WebSocket 接收事件并调用现有 `llm.chat` 回复。SnowLuma 原生 Windows 部署需要本机 NTQQ，首次登录由你在 QQ 窗口扫码；SnowLuma WebUI 默认位于 `http://127.0.0.1:5099/`，OneBot WebSocket 默认 `ws://127.0.0.1:3001/`。在 WebUI 中为机器人账号开启 **WebSocket 服务端**，角色选择 `Universal`，使事件与发送动作共用连接。WebUI 初次启动会给出临时管理员密码，本机首次启动日志在 `C:\CProj\Airi\SnowLuma\snowluma.out.log`；登录后按页面完成协议确认。不要把密码、OneBot token 或 QQ 登录信息提交到仓库。

从 `qq_settings.example.json` 复制为本机 `qq_settings.json`，在 `allowed_private_ids` 填允许私聊机器人的 QQ 号；默认为空，桥接程序会拒绝启动。`allowed_group_ids` 也是显式白名单，默认关闭群聊；启用的群只响应 **@机器人** 的文字消息，不主动插话。若 SnowLuma WebSocket 配置了 token，把 `SNOWLUMA_ACCESS_TOKEN=...` 加到本机 `.env.local`；它与模型 API Key 分开，程序仅用于 WebSocket 鉴权。然后启动 SnowLuma 与机器人 QQ，在本目录双击 `启动QQ桥接.cmd`。先用白名单里的另一个 QQ 号私聊发送“你好”，确认收到回复。QQ 对话的短期历史按发送者隔离。

默认把 1.2 秒内连续发来的短消息合并成一轮；私聊同一发送者的回复间隔至少 5 秒、每小时最多 30 次；群聊同一群至少间隔 12 秒、每小时最多 8 次。消息 ID 去重，所有 QQ 回复全局间隔至少 1.2 秒，单轮最多发一条消息。限额内的短时间后续消息会等待合并；达到小时上限时本轮不回复。桥接不会在群里随机主动发言，也不会在投递结果不明时自动重发，以免重复刷屏。这些阈值都可在 `qq_settings.json` 调整。QQ 文字会发给当前配置的对话模型服务；请只将愿意用于模型对话的账号和群加入白名单。

可选的 `voice_private_ids` 只能填已经在私聊白名单内的 QQ 号，也可在控制台“QQ · SnowLuma”的“语音回复 QQ 号”填写。留空时只有文字。开启后，已启用的思考模型会在每轮给出 `text`/`voice` 建议；短回复（最多 160 字）才有机会用本机 GPT-SoVITS 合成 WAV，经 SnowLuma 的 OneBot `record` 消息段发出。群聊、长回复、思考未启用或信心不足时仍发文字；合成失败时改发文字。语音和文字共用同一套限速，发送结果不明时不会补发，以免重复。需要先在“语音交互”页面选好本地模型、参考片段并启动 9880 服务，保存 QQ 配置后重启桥接。QQ 收到的语音消息目前不会转写；这里只增加语音**输出**。

不用登录真实 QQ 也能先验证接入层：运行 `.\.venv\Scripts\python.exe -m unittest -v test_qq_bridge.py`，测试会在本机模拟 OneBot 连接、消息事件和回复动作，不调用真实 QQ 或模型。

## 统一控制台

双击 `启动界面.cmd` 打开 PySide6 控制台，或运行 `python dashboard.py`。总览显示模型、长期记忆、SnowLuma WebUI、OneBot 本机端口和由控制台启动的桥接进程状态；端口“可连接”只表示端口开放，不等于 QQ 已登录或 OneBot 已完成鉴权。

“模型与人格”可以直接设置供应商、API 地址、模型 ID、密钥、提示词和长期记忆，并从供应商获取模型列表。密钥只写入本机 `.env.local`，保存后不回显；“文字对话测试”和“长期记忆管理”仍打开原有窗口。“QQ · SnowLuma”可保存 OneBot 地址、Token、私聊/群聊白名单和回复频率，启动或停止由控制台管理的 SnowLuma 与 QQ 桥接，并在浏览器打开 SnowLuma 自带的配置页面。默认勾选“启动 SnowLuma 后自动连接”：控制台每三秒检查本机 OneBot 端口，开放后自动启动桥接；桥接自身在 QQ 未就绪、断线或鉴权失败时每五秒重试。首次仍需在 WebUI 登录 QQ、启用 OneBot WebSocket 服务端，并设置与界面一致的 Token。WebUI (`5099`) 用来配置服务，OneBot (`3001`) 用来传送消息，不需要日常手动点击两次。SnowLuma 安装仍在 `C:\CProj\Airi\SnowLuma\v2.0.3`，不进入本仓库。从系统托盘选择退出时会停止由控制台启动的服务；从独立启动脚本运行的服务由其原窗口管理。QQ 桥接在启动时读取配置，修改后需重启桥接生效。

控制台现在有系统托盘图标。点击窗口右上角关闭按钮会把控制台收起到托盘，语音和 QQ 服务继续运行；从托盘菜单可重新打开控制台、打开语音窗口、查看运行过程，或选择“退出 ZhuangFY”真正结束并停止由控制台启动的服务。侧栏与托盘的“重启连接”会在当前界面内重启由控制台管理的 QQ 桥接与 GPT-SoVITS 服务，使它们重新读取本地设置；SnowLuma 保持登录。独立启动的服务不会由此重启，UI Python 代码更新仍须完整退出后重开。系统没有托盘时，关闭窗口仍按原方式退出。

“运行过程”页面按来源和关键词查看**本次启动**的语音识别、用户消息、思考阶段返回内容及解析出的语气/关注点、庄方宜场景选择、模型调用耗时、回复、播报与 QQ 记忆操作。这里的“思考”是程序可见的结构化策略，不是模型内部推理。事件最多在内存中保留 2000 条；由控制台启动的 QQ 桥接通过本机事件通道发送到此页面。新运行不再写入日志文件，退出后本次事件消失。旧版本留下的 `SenseVoice/logs/` 文件不会自动删除，仍由你决定是否清理。明显的密钥会在显示前遮盖。

## 语音窗口

在控制台点击“打开语音窗口”，也可以直接运行 `python gui.py`。窗口中的“麦克风”可以选系统默认设备，也可以指定 Realtek 或其他输入设备；“刷新”会重新读取设备列表。“识别语言”默认是中文，需要时可改为自动识别或其他语言。

点击“开始录音”后说话，再点击“停止并识别”。录音最多 60 秒，文字显示在结果框中，可以一键复制。“试用示例”会读取目录里的中文示例音频，供你先检查模型是否工作。录音只在内存中处理，不会保存麦克风音频文件。

“开启唤醒监听”会一直读取麦克风，但只在 Silero VAD 检测到一段人声并判断说话结束后，才调用 SenseVoice 识别这一句。初始静音结束阈值为 0.8 秒，保留约 0.25 秒的前置声音。启动后显示“待唤醒”：说“庄方宜”“庄天师”或“小庄”会进入对话状态；默认识别别名还包含“庄方怡”“庄芳怡”“庄方一”“庄天使”等常见误写。接下来 30 秒内说的话会显示在结果框中。说“小庄，帮我……”时，该句也会显示为“庄方宜，帮我……”。单独叫名字只唤醒，不发送模型。带内容的唤醒句与后续每句有效讲话，在“唤醒后自动发送给模型”开启时，会去掉句首称呼后发送给模型。每轮回复结束后重新开始 30 秒倒计时，超时后返回待唤醒。原来的手动录音和示例识别仍可用于调试，它们不经过唤醒门控，也不发送模型。

界面中的“唤醒称呼”“识别别名”“对话保持”可修改，失去焦点或关闭窗口时保存到 `settings.json`，下次启动会读取。当前版本的 sherpa-onnx SenseVoice 调用没有通用提示词参数，所以别名校正发生在识别文字之后、对话状态判断之前，不会改变模型原始解码。只有句首的称呼会触发唤醒，避免正文中碰巧出现名字时误唤醒；不在别名列表中的错字不会自动猜测。可根据实际识别结果继续添加别名。

现在模型生成与语音播报期间仍保持麦克风监听，界面显示 `Listening / Thinking / Speaking` 状态。回复收到后按句分段播报：系统语音使用 Qt TextToSpeech；GPT-SoVITS 在播放当前段时预生成下一段。说话打断时会停止当前段，并丢弃剩余段落及预生成结果。如果用户在模型生成期间说了新的一句，旧请求即使稍后返回也不会进入对话历史或播报。主语音窗口保存最近六轮成功对话用于当前续聊，关闭程序或点“清空对话”后清除；长期记忆由模型设置窗口单独控制。

你主要使用扬声器，因此默认选择“扬声器：说唤醒名后打断”：播报期间说“小庄，等等”这样的完整短句，待 VAD 判定句尾并识别后，中断播报并处理新请求。系统语音在扬声器模式的段间留约 1.1 秒；专属语音为减少停顿，会在播放当前段时合成下一段，并在播放结束后尽快衔接。若下一段合成仍未完成，仍需等待。程序会过滤与当前回复相似的识别文字，也要求播报期间的打断句以唤醒称呼开头；这只是回声防护，**不是声学回声消除（AEC）**。环境、人声或音量较大的扬声器仍可能造成漏识别或误触发。耳机模式下可选择“检测到说话立即打断”，它在 VAD 发现语音开始时就停止播报；使用扬声器时不建议选此模式。“立即停止当前回复”按钮始终可在生成或播报时使用。当前是完整 LLM 回复后的分段 TTS，还不是模型 token 流式生成。持续监听由你在界面中手动开启，关闭窗口时会停止采集。

如果窗口没有打开，可在 PowerShell 中运行 `.\.venv\Scripts\python.exe .\gui.py` 查看错误。

## 命令行试用

在 PowerShell 中进入本目录：

```powershell
cd C:\CProj\Airi\Shimano\SenseVoice
$env:PYTHONIOENCODING = 'utf-8'
.\.venv\Scripts\python.exe .\recognize.py --file .\samples\zh.wav --language zh
.\.venv\Scripts\python.exe .\recognize.py --list-devices
.\.venv\Scripts\python.exe .\recognize.py --record --seconds 6 --device 1 --language zh
```

`--device 1` 在这台电脑上是默认的 Realtek 麦克风阵列。设备编号可能随插拔或系统设置改变，请以 `--list-devices` 的最新结果为准。命令行录音启动后立即说话，六秒后显示结果。模型在 CPU 上运行；第一次识别需要加载模型，可能比后续调用慢。

## 代码路线

控制台入口是 `dashboard.py`，负责设置、模块入口和服务启停；语音窗口是 `gui.py` 的 `MainWindow`。它负责麦克风选择、`Listening / Thinking / Speaking` 状态、打断、对话显示和分段 TTS；`RecognitionWorker` 处理单次录音，`ContinuousWorker` 处理持续监听，识别都在后台线程运行。`continuous.py` 的 `StreamingVad` 接受音频帧，返回已经说完的片段。`wake.py` 的 `ConversationGate` 接收每句识别文字，校正句首称呼并维护待唤醒/对话中的超时状态。`interaction.py` 拆分回复语句并过滤疑似播报回声。`chat_worker.py` 在后台发送模型请求；只有当前有效请求的成功回复才进入会话历史。

1. `record()`：以 16 kHz、单声道采集麦克风声音，得到浮点数数组。
2. `speech_spans()`：Silero VAD 找出数组中有人说话的位置，去掉较长的静音。
3. `recognize()`：SenseVoice 对每段语音解码，合并成文字。

`recognize.py` 的 VAD 和解码流程参考了 [Sakura 的 SenseVoice 插件](https://github.com/Rvosy/Sakura/tree/main/plugins/optional/sakura_asr_sensevoice)，其 MIT 许可文本保留在 `third_party/Sakura-LICENSE.txt`。这里使用相同版本的模型、词表和 VAD，但不会上传 Sakura 源码目录或程序包。模型权重、sherpa-onnx 与 VAD 各有自己的许可和来源，见项目根目录的 `THIRD_PARTY.md`。

`samples/zh.wav` 来自 [sherpa-onnx 的 SenseVoice 示例资源](https://huggingface.co/csukuangfj/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17/tree/2365baeacb507f821a0c8120fcee3d484dba7a07/test_wavs)。它用于本地验证；发布或分享样本时应先核对其授权。

本机已验证：示例 WAV 识别为“开放时间早上9点至下午5点。”；Realtek 麦克风可以打开 16 kHz 输入流。真实讲话的识别效果还需你对着麦克风运行上面的录音命令检验。

图形界面也已验证示例识别、设备选择，以及 Realtek 麦克风的开始和停止录音。空白录音会提示“没有检测到人声”，并恢复按钮状态。

持续监听已用分帧示例音频验证：VAD 在句尾静音后输出一个片段，识别结果为“开放时间早上9点至下午5点。”；Realtek 麦克风的持续监听启动与停止也已验证。唤醒、模型请求、暂停与恢复采集的逻辑可运行 `.\.venv\Scripts\python.exe -m unittest -v test_voice_loop.py test_llm.py test_wake.py` 验证。Qt 的 Microsoft Yaoyao 中文发音人已实际播放测试短句，状态从 Speaking 返回 Ready。完整真实人声对话仍需对着麦克风验证，误触发和说话停顿长度也需边使用边调整。

## 情绪与长对话上下文（实验性）

当前的对话层由 `emotion.py`（轻量情绪状态）、`dialogue_context.py`（最近六轮、较早轮次摘录、事件状态）和 `turn_planner.py`（可选的第二次模型分析）组成。三个入口——文字测试、语音窗口、SnowLuma QQ 桥接——都使用同一套上下文格式。模型产生的只是程序模拟的情绪线索，并非人类情感或心理诊断。

### 超过六轮怎么办

仍向回复模型提供最近 **12 条消息（通常是六轮完整问答）**。更早的用户消息会被转为有限长度的**原文摘录**（最多 16 条，总长度约 1900 字符），与尚未解决／用户报告解决的事件状态一同作为独立参考数据发送给模型。该机制不要求额外 API 调用，但**不是语义级滚动摘要**：极长的对话依然会淘汰最旧的摘录，关键事实请使用现有 Mem0 并核实准确性。被打断、请求失败、QQ 投递失败时均不更新会话历史和事件状态。

### 可选双模型阶段

复制 `cognition_settings.example.json` 为本地 `cognition_settings.json`（已加入 Git 忽略），修改如下：

    {"enabled": true, "model": "", "persist_private_ids": [], "persist_desktop_context": false}

`enabled=true` 表示每轮先调用一次模型，要求返回结构化 JSON 的语气和关注点建议，然后再由正常庄方宜人格模型生成可显示的回复。`model` 留空则使用当前主模型；若填写必须是**当前供应商支持的模型 ID**，并使用同一 API Key。普通手动提问、唤醒后的语音、QQ 私聊及被 @ 的群消息均属于明确的对话请求，**思考模型不能拒绝回应**；对未来环境主动事件的 `should_reply` 决定只预留了接口，目前没有自动主动发言能力。

这个选项默认关闭，因为开启会增加每轮模型请求次数、延迟和费用。分析模型超时、返回非 JSON、格式错误时回退为单模型回复。角色提示词和回复模型保持独立。

### QQ 私聊跨重启记忆（需要明确开启）

在 `cognition_settings.json` 的 `persist_private_ids` 中填写**已经出现在 `qq_settings.json` 的 `allowed_private_ids` 中、且明确同意持久化的私聊账号**，例如 `[123456789]`（仅是格式示例）。无需开启 `enabled` 也可使用会话持久化。配置保存后重启 QQ 桥接。只有这些指定私聊账号会把最近六轮、有限旧轮次摘录及事件状态保存到本地 `conversation_data/qq_context.sqlite3`；其他私聊和所有群聊只保存在进程内存，各账号之间严格按 scope 隔离。未配置时不建立数据库。

本地 SQLite **未加密，含有部分对话原文**，拥有电脑访问权限的人可能读取。程序会尽力排除看起来像密钥或密码的内容，但不能保证识别所有秘密。会话记录按最后写入时间在读取时实施 30 天过期清理；并不是自动按时删除所有过期行。可停止 QQ 桥接后删除 `conversation_data/qq_context.sqlite3` 以移除这些会话记录。这个数据库与原有 Mem0 互不混用，QQ 内容不会自动写入桌面主人的 Mem0。

桌面文字／语音窗口默认仅在本次窗口保留上下文。若在 `cognition_settings.json` 中设置 `"persist_desktop_context": true`，文字测试窗口和语音窗口会分别保存到 `conversation_data/desktop_context.sqlite3` 中的 `desktop:text`、`desktop:voice`，重启后自动恢复；两者不会互相串聊。窗口内点“清空对话”会同步删除对应存储记录。数据库同样未加密，关闭持久化开关并不会自动删除之前写入的文件；需要退出程序后手动删除该 SQLite 文件。未来可继续加入经过证据核验的语义摘要，而不是把模型猜测长期写成用户事实。

### 测试

在 `SenseVoice` 目录执行：

    python -m unittest -v test_emotion test_dialogue_context test_turn_planner test_llm test_qq_bridge

另有 GitHub Actions 的 Python 离线检查。真实 Windows 端的 PySide6、麦克风、TTS、SnowLuma 端到端场景仍需单独验收。

## 分用户记忆与庄方宜 Skill 场景路由（第二阶段）

这一阶段参考 [dsh-qqchat](https://github.com/Coffeiz/dsh-qqchat) 的 member scope / 五层资料和证据约束反思，并将 [MengPaul07/zhuang-fangyi-skill](https://github.com/MengPaul07/zhuang-fangyi-skill) 的场景行为规则**改写**进 `fangyi_skill.py`。未复制该仓库的原始台词、场景正文或 Wiki 数据；`persona_packs/zhuang_fangyi_v1/prompt.md` 继续负责稳定人物底色，场景路由只在问题涉及相关情境时附加一条短建议。不会把用户当作剧情管理员，也不会把原作故事记入用户个人档案。

### 配置

在 `SenseVoice` 目录将 `cognition_settings.example.json` 复制为 `cognition_settings.json`（本地配置，Git 忽略）。例如：

```json
{
  "enabled": false,
  "model": "",
  "persist_private_ids": [],
  "persist_desktop_context": false,
  "memory_private_ids": [],
  "fangyi_scene_skill": true
}
```

- `fangyi_scene_skill` 控制庄方宜行为路由；只对匹配庄方宜人格包或明确以她为对象的长提示词生效。默认启用，不增加新的模型请求。
- `memory_private_ids` 是**新的独立同意名单**，默认空。只有这里列出的、同时存在于 `qq_settings.json.allowed_private_ids` 中的 QQ 账号，在私聊时会建立个人档案并发送内容给记忆反思模型。群聊不进入个人档案，即使该用户也在个人记忆名单内也不例外。私聊个人资料以（机器人账号 ID、QQ 发送者 ID）联合区分，昵称只用于展示。
- `persist_private_ids` 仍只控制上一阶段的会话历史持久化，和 `memory_private_ids` 互不依赖，也不会把其他 QQ 用户的资料写入桌面主人的 Mem0。
- `enabled` 仍只控制每轮调用可选的思考模型；它与是否启用记忆反思是两个开关。

重启 QQ 桥接后配置生效。当前控制台尚无这些开关的图形编辑入口，需要编辑本地 JSON。

### 记忆工作流程

只在 QQ 私聊回复**确认投递成功**后，才记录该用户这一轮输入与 Bot 回复到待提炼队列。每积累至少六轮，后台发起一次额外的 LLM 请求，整理为五类：`profile`（稳定事实）、`pattern`（重复模式）、`summary`（近期状态）、`daily`（阶段性重要事件）、`memory`（高价值长期经历）。返回信息要附带用户 `inbound` 原文中的证据片段；行为模式必须来自至少两条不同的用户发言。Bot 回复、引用文字及其他人的话不应成为用户身份事实。证据校验能过滤明显的无来源内容，但**不能保证模型一定正确理解复杂的转述、讽刺或矛盾**，这是后续需要改进的部分。

当前会员记忆独立存于 `conversation_data/qq_members.sqlite3`，存储的是分层资料、证据片段以及最多七天的未反思消息；它**未经加密**。后台模型服务和主回复模型使用相同供应商及密钥，可能增加 API 费用。单次反思失败时保留未处理消息，不覆盖已有档案，下次再重试。用户在自己的 QQ 私聊中发送 **`/forget_me`** 或 **`/删除我的记忆`**，可清除该机器人账号下自己的成员档案、待提炼消息，以及当前会话上下文。也可以在停止服务后删除相应本地 SQLite 文件以整体清空。

记忆快照按当前用户读取，作为**不可信的历史参考数据**放在正常聊天历史前；仍以本轮用户明确陈述为准。没有启用个人记忆时，行为与原先保持一致。原有桌面 Mem0 仍独立运行。

### 语音交互与 QQ 个人记忆

控制台新增“语音交互”页面：选择麦克风，开启/停止唤醒监听，做单句录音、示例音频识别及播报测试，并查看识别和回复文字。与原有语音窗口共用同一个采集和对话实例；可从页面打开完整语音窗口调整唤醒词、别名、超时与打断方式。

页面中的“关联到当前机器人”会把当前 OneBot 登录的机器人 QQ 号与你的个人 QQ 号写入本机 `owner_link.json`。个人 QQ 必须同时在私聊白名单和 `cognition_settings.json` 的 `memory_private_ids` 内；不满足时拒绝关联。机器人登录后会写入本机 `qq_identity.json`。若登录了另一个机器人账号，关联会失效，避免读到错误用户资料。这两个关联文件不会提交 Git。

关联后，**语音和 QQ 共用** `conversation_data/qq_members.sqlite3` 中同一个（机器人 QQ 号、用户 QQ 号）档案。每次语音生成回复前读取已提炼的档案，以及最近最多四条你自己说过、尚未提炼的话，方便刚回家时接上话题；这些摘录只是可能过期的参考，不是本轮新指令。只有确认接受的回复才把语音轮次加入同一待提炼队列，累计六轮才由后台额外请求模型反思。被打断而舍弃的旧回复不写入。QQ 与语音的完整短期会话上下文仍分别维护。原有桌面 Mem0 不迁移、不删除，也不与 QQ 档案混为一谈。资料库仍未加密；请保护 `conversation_data/`。

### GPT-SoVITS v2Pro 专属语音（可选）

引擎独立安装于 `C:\CProj\Airi\GPT-SoVITS`，使用[官方 GPT-SoVITS v2Pro 发布版本](https://github.com/RVC-Boss/GPT-SoVITS/releases/tag/20250606v2pro)；源码、Python 环境与权重不纳入 ZhuangFY 仓库。语音引擎保存为 GPT-SoVITS 后，重新打开控制台会自动启动本机 API 服务；保存语音设置时若服务未运行，也会尝试启动。服务默认监听 `127.0.0.1:9880`，首次加载模型可能需要几十秒。Windows 上为避开虚拟环境启动器的路径解析错误，控制台会在 GPT-SoVITS 的 `.venv/Scripts` 中准备一个同版本的 `python-gpt-sovits.exe`；它仍使用该虚拟环境中的依赖，需要原始 Python 安装的运行库。控制台“语音交互”页面仍可手动启停由它启动的服务；若端口上已有独立启动的服务，则直接复用，不重复启动。未配置参考音频时仍使用 Windows 系统语音。若要切换，选择清晰的 5–10 秒 WAV/MP3/FLAC 片段，准确填写其台词，再将引擎设为“GPT-SoVITS v2Pro”并保存。参考音频的使用需符合其来源许可。

“本地模型”下拉框会扫描官方 v2Pro 底模，以及本机已经成对训练出的 GPT/SoVITS 权重；每套训练实验显示最新的轮次。选择“庄方宜 · 已训练”会自动填入已核对的参考音频和台词。**训练权重并不取代参考片段**：当前使用的 GPT-SoVITS v2Pro `/tts` 接口要求 `ref_audio_path`，参考录音还会影响音色细节与语气；`prompt_text` 在接口中可选，但程序要求准确台词以保持稳定的推理效果。正常使用庄方宜模型时不必反复选择，程序会填好这两个字段。换成官方底模或其他训练模型时，需自行选参考片段。点击“应用模型与语音设置”后，若本地服务正在运行，界面会直接调用其权重切换接口；未运行时写入启动配置。系统语音发音人选择已从界面收起，系统语音仍作为备用引擎；“播报测试”会走当前选定的引擎。

本机庄方宜音频位于 `C:\CProj\Airi\zhaungfyvoice`（沿用现有目录拼写）。`tools/prepare_voice_dataset.py` 从[华法琳 Wiki 的庄方宜台词页](https://warfarin.wiki/cn/operators/zhuang-fangyi#dialogue)按文件名匹配台词，将 MP3 转为 32 kHz、单声道、16 位 WAV，生成 `prepared/manifest.csv` 供人工校对及 `prepared/training.list` 供官方训练脚本读取。原 MP3 保留。当前按时长仅把 1–20 秒的条目列入训练；长台词需要先切分并校对对应子句，过短条目不使用。下载的原始语音、转换文件、台词清单和训练权重均留在 Git 仓库外。

需要重新准备或微调时，在 `SenseVoice` 目录使用 GPT-SoVITS 的 Python 环境：

准备脚本额外使用 `beautifulsoup4` 与 `imageio-ffmpeg`；在新的引擎环境中先安装这两个包。

```powershell
& 'C:\CProj\Airi\GPT-SoVITS\.venv\Scripts\python.exe' tools\prepare_voice_dataset.py 'C:\CProj\Airi\zhaungfyvoice'
& 'C:\CProj\Airi\GPT-SoVITS\.venv\Scripts\python.exe' tools\train_zhuangfy_v2pro.py prepare
& 'C:\CProj\Airi\GPT-SoVITS\.venv\Scripts\python.exe' tools\train_zhuangfy_v2pro.py train
& 'C:\CProj\Airi\GPT-SoVITS\.venv\Scripts\python.exe' tools\train_zhuangfy_v2pro.py activate
```

预处理数据和日志位于 `C:\CProj\Airi\GPT-SoVITS\logs\zhuangfangyi_v2pro`；训练完成的小模型位于引擎目录的 `SoVITS_weights_v2Pro` 与 `GPT_weights_v2Pro`。`activate` 将最新一对权重写入本机 API 配置，并用已匹配的参考音频及台词启用语音窗口。`manifest.csv` 的台词来自网页，建议在增加长台词切分或再次训练前听录音核对。

新引擎保持现有分段播报。每段向本机 `/tts` 发送文字、参考音频路径和台词，再播放返回的 WAV；当前段开始播放时会立即请求下一段，并在内存中最多预存一段。打断会中止请求、停止播放并清除预存音频。服务不可用或返回格式错误时自动退回系统语音，文字回复仍可查看。当前使用非流式分段合成，首字延迟取决于本机模型速度；如果下一段生成时间长于当前段播放时间，段间仍会有等待。设置保存在被 Git 忽略的 `gpt_sovits_settings.json`。

### 与原始庄方宜 Skill 的关系

本次只移植**行为规则与场景迁移方法**，尚未导入全部十五幕剧情卡、知识树和对白原文。几个代表性场景：

- 技术研究对应场景 05 的协作与判断；挫败和失落对应场景 09 的体谅与继续前行；
- 分享阶段成果对应场景 02/14 的认真祝贺和允许休息；
- 决策风险对应北进场景 11/12 的准备与共同承担；
- 日常与亲近对应场景 03/10/14 的生活气息和有分寸的关心；
- 原作设定问答按 `references/epistemics.md` 的原则区分官设、推断与演绎。

这些都是**现实场景的行为迁移**，不能据此宣称角色已经与现实用户共同经历过任何剧情。原仓库 MIT 署名见 `third_party/Zhuang-Fangyi-Skill-LICENSE.txt`。

### 测试与限制

无需 QQ 登录和真实模型 Key，运行：

```bash
python -m unittest -v test_emotion test_dialogue_context test_turn_planner test_llm test_qq_bridge test_member_memory test_owner_memory test_voice_loop test_fangyi_skill
```

GitHub Actions 也会执行离线检查。真实 QQ 的权限、数据库读写、API 费用与语音端到端表现仍需本机验证。此版本还没有完整的用户档案编辑 GUI 或自动更正冲突的语义验证；桌面主人身份只通过明确的账号关联确认，不自动推断。后台反思只保证结构和证据门槛，不能证明角色有真实的主观情绪。


## ZhuangFY 模型 API 容灾（分阶段候选）

在**统一控制台 → 模型与人格 → 分阶段模型 API 与容灾 → 配置**（或独立文字测试窗口的“配置分阶段 API…”）设置不同任务的模型调用链。此功能不要求把 SnowLuma、GPT-SoVITS 与主程序放入一个进程。

- **思考 Planner**：可选开启。主候选失败后按照顺序尝试备用 API。响应不是合法的 Planner JSON 也视为失败并切换。所有候选失败后跳过 Planner，直接执行回复模型；不能让 Planner 阻止用户主动提出的问题。
- **回复 Reply**：主候选出现临时网络错误、HTTP 429/5xx、响应不完整或空回复时执行有限重试，随后尝试备用 API。全部失败时返回可诊断的失败状态；不会虚构正常的角色回复。
- **识图 Vision**：目前只预留模型候选和重试配置，尚未完成多模态图片消息的调用与解析，不能将这个开关视为识图功能已可用。

每一组可以有 0–5 条候选，使用“↑ 优先”和“↓ 次选”调整顺序；每条候选独立设置**供应商、API Base URL、模型 ID、API Key**。组级配置包含**额外重试次数（0–3）、首次重试间隔、单次请求超时和阶段总等待预算**。重试间隔按指数退避增长，最长单次等待 8 秒。默认思考组单次 12 秒、总预算 35 秒；回复组单次 25 秒、总预算 85 秒。配置候选为空时会回退到原有 `llm_settings.json` 的供应商及模型；原有思考阶段的 `model` 则用作 Planner 的旧版回退模型 ID。

保存后自动生成本地 `model_routes.json`（已加入 Git 忽略）；此文件只有模型 ID、地址、顺序和重试策略，**没有密钥**。每个候选的专用密钥保存在 `.env.local`，格式为 `ZHUANGFY_ROUTE_REPLY_1_API_KEY` 等，并与配置时的**供应商和完整 API 地址绑定**。切换供应商或地址后旧的专用密钥不会自动复用，需要重新填写。官方固定地址的供应商仍可使用其原有供应商密钥；自定义兼容服务则需单独设置候选密钥，或使用无需认证的本地服务。若未生成 `model_routes.json`，原有单模型配置完全保留。可参考 `model_routes.example.json`。

**隐私和费用：** 当首选服务出错后，备用服务也会接收相同的对话原文、当前角色提示词与已选择的记忆资料；请只配置你信任的备用供应商。请求超时并不保证远端没有完成推理，重试可能带来重复计费。不会为一次失败自动多次发送 QQ 消息。同步 HTTP 超时主要限制单次套接字操作，不能代替未来需要实现的跨阶段可取消总时限。

**无法回复时：** 桌面文字测试保留原输入；语音监听界面保留失败的识别文字，并提供“重试上一条失败的语音文字”，无需重新进行 ASR。QQ **私聊**在回复模型候选全部失败时尝试发一条明确的系统提示，并在本进程当前会话内保存该条失败输入，之后发 `/retry` 可以重试；提示投递失败或 QQ 连接断开时仍会记录模型失败状态，不能保证系统提示被 QQ 成功投递。这个状态暂未跨程序重启持久化；达到 QQ 限流或 SnowLuma 投递结果不明确仍需要后续专项修复。

**测试：** `python -m unittest -v test_model_routes test_llm test_qq_bridge` 可离线模拟 API 失败、备用候选成功、认证失败不原地重试、Planner 格式错误、密钥隔离和 QQ 手动重试。GitHub Actions 会在 PR 和 main 推送时执行核心回归；实际 Windows 设备与云 API 需要本机端到端验收。
