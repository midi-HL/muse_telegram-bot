[English](README.md) | [简体中文](README.zh-CN.md) | **日本語**

# telegram-bot-gateway

**AI エージェントのためのローカル受信箱としての Telegram Bot。** Bot に
メッセージ——テキスト、写真、ドキュメント、ボイスメモ——を送ると、
すべてが構造化された自己完結型のアイテムとして
ディスクに保存されます。ウォッチャーがエージェントを起こし、
エージェントはアイテムを読み（音声はローカルで文字起こし済み、
画像はマルチモーダル入力用にファイルとして用意されます）、
Bot 経由で返信し、処理済みにマークします。

- **依存ゼロ** — Python 3 標準ライブラリのみ。pip は不要です。
  （例外として、curl があればダウンロードに優先使用します。一部の
  プロキシは urllib のダウンロードを途中で切断するためで、urllib
  へのフォールバックもあります。）
- **ローカル ASR** — ボイスメモは sherpa-onnx + Paraformer
  中英バイリンガルモデルで、このマシン上で文字起こしされます。
  音声がマシンの外に出ることはありません。
- **クラッシュセーフ設計** — 受信箱への書き込みはアトミックで、
  getUpdates のオフセットは永続化され、障害はクラッシュさせずに
  記録内に残されます。
- **明示的な信頼モデル** — chat-id の許可リストによって
  「所有者のメッセージ」と「他人のデータ」を分離します。
  許可リストが空 = 何も信頼しません。

ステータス：**v0.1.0 — 構築済みで、モックテストを完全に通過しています。
アクティベーションは別途、意図的に行うステップです**
（[docs/activation.md](docs/activation.md) 参照）。

## 仕組み

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

全体図と不変条件：[docs/architecture.md](docs/architecture.md)。

## 機能

- 長時間ポーリングによる `getUpdates` 受信箱サービス（`gateway.py`）
- メディア受信：**写真**（最大サイズ）、**ドキュメント**、
  **動画**、**ビデオノート**、**音声**、**ボイス**、**アニメーション**、
  **ステッカー** — `getFile` でダウンロードし、記録に
  種類、パス、MIME タイプ、サイズを明記します
- 音声の文字起こし結果を記録に保存（`transcript`）
- Wake-to-answer ツール群（`process_inbox.py list|show|ack`）
- 保持期間のクリーンアップ：`tmp/` の一時ファイルは 24 時間、
  `processed/` は 7 日間（設定可能）。サービス本体と
  `cleanup.py` が実施します
- 返信用 CLI（`send.py`、4096 文字で長文を自動分割）
- systemd ユーザーユニット同梱
- モック Bot API に対する、トークン不要のテストスイート（14 項目）

## クイックスタート

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

## 設定

| 場所 | 内容 |
|---|---|
| `.env` | `BOT_TOKEN`（必須）、`TELEGRAM_API_BASE`（任意。セルフホストの Bot API を指すと 20 MB のダウンロード上限を解除できます） |
| `config.json` | `allowlist_chat_ids`、`poll_timeout_seconds`、`transcribe_voice`、`asr_threads`、`tmp_retention_hours`、`processed_retention_days` |

`.env.example` / `config.example.json`、または完全な
[設定リファレンス](docs/configuration.md) を参照してください —
すべての変数とキーについて型、デフォルト値、例を記載し、
グループ向けの BotFather プライバシーモードに関する注記も
含まれます。

## ファイル構成

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

## テスト

```bash
python3 tests/mock_test.py
# 14/14 checks passed — runs the real gateway against a local mock
# Bot API: text/photo/document/voice items, failed-download handling,
# offset persistence, process_inbox list/ack, retention cleanup,
# sendMessage payload, placeholder-token refusal.
```

ASR にはモデル付属のテスト wav を使った実テストがあります —
実測の出力と所要時間は [docs/asr.md](docs/asr.md) に記載されています。

## ドキュメント

- [AI エージェントでインストール](docs/agent-install.md) —
  インストール全体をエージェントに任せる、そのまま貼れる
  プロンプト。人間向けチェックリストと障害メモ付き
- [アーキテクチャ](docs/architecture.md) — データフロー、不変条件、
  wake-to-answer 仕様、ストレージのライフサイクル
- [アクティベーション](docs/activation.md) — BotFather → トークン → サービス →
  ウォッチャー → 許可リスト、ステップバイステップ
- [設定](docs/configuration.md) — すべての `.env` 変数と
  `config.json` キー、デフォルト値と例付き
- [ASR](docs/asr.md) — sherpa-onnx/Paraformer スタックと実測結果
- [セキュリティ](docs/security.md) — トークンの扱い、信頼モデル、
  悪意ある入力へのルール
- **翻訳** — この README には [English](README.md) 版と
  [简体中文](README.zh-CN.md) 版もあります

## 制限事項

- **公開 Bot API の 20 MB ダウンロード上限**
  （`api.telegram.org`）：これより大きいファイルは
  `download_error` を含む記録として届きます。
  `TELEGRAM_API_BASE` にセルフホストの Bot API サーバーを
  指定すると上限を解除できます。
- **起動までの遅延は最大で約 1 分**：長時間ポーリングにより
  メッセージは受信箱へ即座に届きますが、ウォッチャーは
  1 分ごとに実行されるため、エージェントが起きるのは
  送信から最大で 1 分後になります。
- **ASR は小型のオンデバイスモデル**：時々誤りが出ます。
  特に中国語と英語の混ざった発話で顕著であり、背景ノイズや
  遠くのマイクではさらに悪化します。利便性のためのものであり、
  法廷記録ではありません。
- **1 つの Bot につきポーラーは 1 つだけ**：同じトークンに対して
  `getUpdates` を実行できるゲートウェイインスタンスは 1 つだけです —
  2 つ目は Telegram から `409 Conflict` を受けます。
- **Python 3.10+、標準ライブラリのみ**：pip インストールは
  不要ですが、新しいランタイムの機能も使えません。
  重いのは ASR だけです。ダウンロードは curl（あれば）を優先し、
  バイト数を検証するため、切断された転送は再試行され、壊れた
  ファイルが黙って保存されることはありません。
- **ASR スタックは linux-x86_64 向け**：`scripts/setup_asr.sh` が
  インストールするプリビルド済みの sherpa-onnx ランタイム
  バイナリは linux-x86_64 向けであり、`ffmpeg` が PATH に
  必要です。他のプラットフォームでは対応する sherpa-onnx
  ビルドが必要です（または `transcribe_voice: false`）。
- **音声の文字起こしはローカルのみ**：音声がクラウド ASR
  サービスへ送られることは一切ありません。ローカルモデルが
  未インストールの場合、ボイスメモは文字起こしなしで
  保存されます。
- **ライセンスは [CC BY-NC-SA 4.0](LICENSE)**：表示と
  同一ライセンスを条件に、自由に利用、改変、共有できますが、
  **商用利用は許可されません**。

## ライセンス

[CC BY-NC-SA 4.0](LICENSE) — 表示と同一ライセンスを条件に、
自由に利用、改変、共有できますが、**商用利用は
許可されません**。プロジェクト所有者が選択しました。
