[English](README.md) | **简体中文** | [日本語](README.ja.md)

# telegram-bot-gateway

**一个 Telegram 机器人，用作 AI 智能体的本地收件箱。** 你给
机器人发消息——文字、照片、文档、语音——所有内容都会以
结构化、自包含的条目形式落到磁盘上。一个 watcher 负责唤醒
智能体，智能体读取这些条目（语音已在本地转写好，图片以文件
形式备好，供多模态读入），通过机器人回复，并把条目标记为
已处理。

- **零依赖**——只用 Python 3 标准库。不需要 pip。（一个例外：
  如装有 curl，文件下载优先用 curl——某些代理会把 urllib 的
  下载流中途截断——并以 urllib 兜底。）
- **本地 ASR**——语音条在本机用 sherpa-onnx + Paraformer
  中英双语模型转写。音频不离开本机。
- **按防崩溃设计**——收件箱原子写入、getUpdates 偏移量
  持久化，故障在记录内标注，而不是让服务崩溃。
- **明确的信任模型**——通过 chat-id 白名单区分「机主的
  消息」与「陌生人的数据」。白名单为空 = 什么都不信。

状态：**v0.1.0——已构建并通过完整的 mock 测试；激活是
单独的、需刻意执行的步骤**（见 [docs/activation.md](docs/activation.md)）。

## 工作原理

```
Telegram user ──► Bot API ──► gateway.py ──► inbox/<update_id>/
                                                  ├── message.json
                                                  └── attachments…
                                                        │
                              watcher (1 min) ──► agent wakes, reads,
                                                  replies via send.py,
                                                  acks via process_inbox.py
                                                        │
                                                        ▼
                                                 processed/<id>/
```

完整图示与不变量说明：[docs/architecture.md](docs/architecture.md)。

## 功能

- 长轮询 `getUpdates` 收件箱服务（`gateway.py`）
- 媒体接收：**照片**（最大尺寸）、**文档**、
  **视频**、**视频笔记**、**音频**、**语音**、**动画**、
  **贴纸**——通过 `getFile` 下载，在记录中逐项列明
  类型、路径、MIME 类型和大小
- 语音转写结果存入记录（`transcript`）
- 唤醒即答工具（`process_inbox.py list|show|ack`）
- 保留期清理：`tmp/` 临时文件 24 小时、`processed/` 7 天
  （可配置），由服务本身和 `cleanup.py` 执行
- 回复 CLI（`send.py`，长文本按 4096 字符自动拆分）
- 附带 systemd 用户单元
- 免 token 的测试套件，针对 mock Bot API（14 项检查）

## 快速开始

```bash
git clone <this repo> && cd telegram-bot-gateway

# 1) Configure
cp .env.example .env            # put your BotFather token in .env
chmod 600 .env
cp config.example.json config.json   # optional; defaults work

# 2) (Optional) local voice transcription
scripts/setup_asr.sh            # ~310 MB of downloads into asr/
# needs ffmpeg on PATH at runtime

# 3) Run in the foreground to sanity-check
python3 gateway.py              # logs "gateway starting as @<bot>"

# 4) Or install as a service — see docs/activation.md
```

## 配置

| 位置 | 内容 |
|---|---|
| `.env` | `BOT_TOKEN`（必填）、`TELEGRAM_API_BASE`（可选；指向自托管的 Bot API 即可解除 20 MB 下载上限） |
| `config.json` | `allowlist_chat_ids`、`poll_timeout_seconds`、`transcribe_voice`、`asr_threads`、`tmp_retention_hours`、`processed_retention_days` |

见 `.env.example` / `config.example.json`，或完整的
[配置参考](docs/configuration.md)——每个变量和配置项的
类型、默认值与示例，外加群组中 BotFather 隐私模式的
注意事项。

## 文件布局

```
telegram-bot-gateway/
├── gateway.py                 # long-polling inbox service
├── send.py                    # reply CLI (sendMessage)
├── process_inbox.py           # agent-side list / show / ack
├── cleanup.py                 # retention cleanup (also in-service)
├── transcribe.py              # sherpa-onnx ASR wrapper
├── config.json                # live config (see config.example.json)
├── .env                       # secrets — chmod 600, gitignored
├── inbox/  processed/  tmp/  state/     # runtime data, gitignored
├── asr/                       # runtime + models, gitignored
├── scripts/setup_asr.sh       # reproduces the ASR install
├── docs/                      # architecture, activation, asr, security
├── tests/mock_test.py         # token-free end-to-end tests
└── telegram-bot-gateway.service
```

## 测试

```bash
python3 tests/mock_test.py
# 14/14 checks passed — runs the real gateway against a local mock
# Bot API: text/photo/document/voice items, failed-download handling,
# offset persistence, process_inbox list/ack, retention cleanup,
# sendMessage payload, placeholder-token refusal.
```

ASR 有自己的一套真实测试，使用模型自带的测试 wav——
实测输出与耗时见 [docs/asr.md](docs/asr.md)。

## 文档

- [用 AI 智能体安装](docs/agent-install.md)——一段可以
  直接粘贴的提示词，把整个安装交给智能体，
  外加一份人工检查清单和故障说明
- [架构](docs/architecture.md)——数据流、不变量、
  唤醒即答规范、存储生命周期
- [激活](docs/activation.md)——BotFather → token → 服务 →
  watcher → 白名单，一步步走
- [配置](docs/configuration.md)——每个 `.env` 变量
  和 `config.json` 配置项，含默认值与示例
- [ASR](docs/asr.md)——sherpa-onnx/Paraformer 技术栈与实测结果
- [安全](docs/security.md)——token 处理、信任模型、
  恶意输入处理规则
- **其他语言**——本 README 另有 [English](README.md)
  和 [日本語](README.ja.md) 版本

## 限制

- **公开 Bot API 的 20 MB 下载上限**
  （`api.telegram.org`）：超过上限的文件会以带
  `download_error` 的记录形式到达。把 `TELEGRAM_API_BASE`
  指向自托管的 Bot API 服务器即可解除该上限。
- **唤醒延迟最长约 1 分钟**：长轮询会把消息
  立即送达收件箱，但 watcher 每分钟才运行一次，所以
  智能体可能在你发出消息后最长一分钟才醒来。
- **ASR 是小型本地模型**：偶尔会转错，
  尤其是中英混合语音；背景噪音和远距离
  麦克风会让情况更糟。它图的是方便，不是
  法庭记录。
- **每个机器人只能有一个轮询者**：同一个 token 只能由
  一个网关实例执行 `getUpdates`——第二个实例会从
  Telegram 收到 `409 Conflict`。
- **Python 3.10+，仅标准库**：不用 pip 安装，但
  也用不上更新运行时的特性；ASR 是唯一的大件。下载会优先用
  curl（如已安装）并校验字节数，截断的传输会重试，绝不静默存坏文件。
- **ASR 技术栈为 linux-x86_64**：`scripts/setup_asr.sh` 安装的
  预编译 sherpa-onnx 运行时二进制面向
  linux-x86_64，且 `ffmpeg` 必须在 PATH 中。其他平台需要
  匹配的 sherpa-onnx 构建（或设置 `transcribe_voice: false`）。
- **语音转写仅在本地进行**：音频永远不会发送到
  云端 ASR 服务。如果本地模型未安装，语音条
  会以未转写的状态存储。
- **许可证为 [CC BY-NC-SA 4.0](LICENSE)**：可自由使用、修改
  并在署名且遵循相同许可证的条件下分享；**不允许
  商业使用**。

## 许可证

[CC BY-NC-SA 4.0](LICENSE)——可自由使用、修改和分享，
但须署名并遵循相同许可证；**不允许商业使用**。
由项目所有者选定。
