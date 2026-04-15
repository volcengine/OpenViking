<div align="center">

<a href="https://openviking.ai/" target="_blank">
  <picture>
    <img alt="OpenViking" src="docs/images/ov-logo.png" width="200px" height="auto">
  </picture>
</a>

### OpenViking: AIエージェントのためのコンテキストデータベース

[English](README.md) / [中文](README_CN.md) / 日本語

<a href="https://www.openviking.ai">Webサイト</a> · <a href="https://github.com/volcengine/OpenViking">GitHub</a> · <a href="https://github.com/volcengine/OpenViking/issues">Issues</a> · <a href="https://www.openviking.ai/docs">ドキュメント</a>

[![][release-shield]][release-link]
[![][github-stars-shield]][github-stars-link]
[![][github-issues-shield]][github-issues-shield-link]
[![][github-contributors-shield]][github-contributors-link]
[![][license-shield]][license-shield-link]
[![][last-commit-shield]][last-commit-shield-link]

👋 コミュニティに参加しよう

📱 <a href="./docs/en/about/01-about-us.md#lark-group">Larkグループ</a> · <a href="./docs/en/about/01-about-us.md#wechat-group">WeChat</a> · <a href="https://discord.com/invite/eHvx8E9XF3">Discord</a> · <a href="https://x.com/openvikingai">X</a>

<a href="https://trendshift.io/repositories/19668" target="_blank"><img src="https://trendshift.io/api/badge/repositories/19668" alt="volcengine%2FOpenViking | Trendshift" style="width: 250px; height: 55px;" width="250" height="55"/></a>

</div>

---

## 概要

### エージェント開発における課題

AI時代において、データは豊富ですが、高品質なコンテキストは得がたいものです。AIエージェントを構築する際、開発者はしばしば以下の課題に直面します：

- **断片化されたコンテキスト**: メモリはコードに、リソースはベクトルデータベースに、スキルは散在しており、統一的な管理が困難です。
- **急増するコンテキスト需要**: エージェントの長時間タスクは実行のたびにコンテキストを生成します。単純な切り詰めや圧縮は情報の損失につながります。
- **検索効果の低さ**: 従来のRAGはフラットなストレージを使用し、グローバルな視点が欠けているため、情報の全体的なコンテキストを理解することが困難です。
- **観察不能なコンテキスト**: 従来のRAGの暗黙的な検索チェーンはブラックボックスのようで、エラー発生時のデバッグが困難です。
- **限定的なメモリの反復**: 現在のメモリはユーザーとのやり取りの記録に過ぎず、エージェント関連のタスクメモリが不足しています。

### OpenVikingのソリューション

**OpenViking**は、AIエージェント専用に設計されたオープンソースの**コンテキストデータベース**です。

私たちは、エージェントのためのミニマリストなコンテキストインタラクションパラダイムを定義し、開発者がコンテキスト管理の煩雑さから完全に解放されることを目指しています。OpenVikingは従来のRAGの断片化されたベクトルストレージモデルを捨て、革新的に**「ファイルシステムパラダイム」**を採用して、エージェントに必要なメモリ、リソース、スキルの構造化された組織を統一します。

OpenVikingを使えば、開発者はローカルファイルを管理するようにエージェントの頭脳を構築できます：

- **ファイルシステム管理パラダイム** → **断片化を解決**: ファイルシステムパラダイムに基づく、メモリ、リソース、スキルの統一的なコンテキスト管理。
- **階層型コンテキストローディング** → **トークン消費を削減**: L0/L1/L2の3層構造、オンデマンドでロードし、コストを大幅に削減。
- **ディレクトリ再帰検索** → **検索効果を向上**: ネイティブのファイルシステム検索手法をサポートし、ディレクトリ位置決めとセマンティック検索を組み合わせて、再帰的で精密なコンテキスト取得を実現。
- **可視化された検索軌跡** → **観察可能なコンテキスト**: ディレクトリ検索軌跡の可視化をサポートし、ユーザーが問題の根本原因を明確に観察し、検索ロジックの最適化を導くことを可能に。
- **自動セッション管理** → **コンテキストの自己反復**: 会話中のコンテンツ、リソース参照、ツール呼び出しなどを自動的に圧縮し、長期メモリを抽出して、使うほどエージェントを賢く。

---

## クイックスタート

### 前提条件

OpenVikingを始める前に、環境が以下の要件を満たしていることを確認してください：

- **Pythonバージョン**: 3.10以上
- **Goバージョン**: 1.22以上（AGFSコンポーネントのビルドに必要）
- **C++コンパイラ**: GCC 9以上 または Clang 11以上（コア拡張のビルドに必要）
- **オペレーティングシステム**: Linux、macOS、Windows
- **ネットワーク接続**: 安定したネットワーク接続が必要（依存関係のダウンロードとモデルサービスへのアクセスのため）

### 1. インストール

#### Pythonパッケージ

```bash
pip install openviking --upgrade --force-reinstall
```

#### Rust CLI（オプション）

```bash
curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/main/crates/ov_cli/install.sh | bash
```

またはソースからビルド：

```bash
cargo install --git https://github.com/volcengine/OpenViking ov_cli
```

### 2. モデルの準備

OpenVikingには以下のモデル機能が必要です：
- **VLMモデル**: 画像とコンテンツの理解用
- **Embeddingモデル**: ベクトル化とセマンティック検索用

#### サポートされているVLMプロバイダー

OpenVikingは3つのVLMプロバイダーをサポートしています：

| プロバイダー | 説明 | APIキーの取得 |
|----------|-------------|-------------|
| `volcengine` | Volcengine Doubaoモデル | [Volcengineコンソール](https://console.volcengine.com/ark/region:ark+cn-beijing/overview?briefPage=0&briefType=introduce&type=new&utm_content=OpenViking&utm_medium=devrel&utm_source=OWO&utm_term=OpenViking) |
| `openai` | OpenAI公式API | [OpenAIプラットフォーム](https://platform.openai.com) |

#### プロバイダー固有の注意事項

<details>
<summary><b>Volcengine（Doubao）</b></summary>

Volcengineはモデル名とエンドポイントIDの両方をサポートしています。簡便さのためモデル名の使用を推奨します：

```json
{
  "vlm": {
    "provider": "volcengine",
    "model": "doubao-seed-2-0-pro-260215",
    "api_key": "your-api-key",
    "api_base": "https://ark.cn-beijing.volces.com/api/v3"
  }
}
```

エンドポイントIDも使用できます（[Volcengine ARKコンソール](https://console.volcengine.com/ark/region:ark+cn-beijing/overview?briefPage=0&briefType=introduce&type=new&utm_content=OpenViking&utm_medium=devrel&utm_source=OWO&utm_term=OpenViking)で確認）：

```json
{
  "vlm": {
    "provider": "volcengine",
    "model": "ep-20241220174930-xxxxx",
    "api_key": "your-api-key",
    "api_base": "https://ark.cn-beijing.volces.com/api/v3"
  }
}
```

</details>

<details>
<summary><b>OpenAI</b></summary>

OpenAIの公式APIを使用：

```json
{
  "vlm": {
    "provider": "openai",
    "model": "gpt-4o",
    "api_key": "your-api-key",
    "api_base": "https://api.openai.com/v1"
  }
}
```

カスタムのOpenAI互換エンドポイントも使用できます：

```json
{
  "vlm": {
    "provider": "openai",
    "model": "gpt-4o",
    "api_key": "your-api-key",
    "api_base": "https://your-custom-endpoint.com/v1"
  }
}
```

</details>

### 3. 環境設定

#### サーバー設定テンプレート

設定ファイル `~/.openviking/ov.conf` を作成します。コピー前にコメントを削除してください：

```json
{
  "storage": {
    "workspace": "/home/your-name/openviking_workspace"
  },
  "log": {
    "level": "INFO",
    "output": "stdout"                 // ログ出力: "stdout" または "file"
  },
  "embedding": {
    "dense": {
      "api_base" : "<api-endpoint>",   // APIエンドポイントアドレス
      "api_key"  : "<your-api-key>",   // モデルサービスAPIキー
      "provider" : "<provider-type>",  // プロバイダータイプ: "volcengine" または "openai"（現在サポート済み）
      "dimension": 1024,               // ベクトル次元
      "model"    : "<model-name>"      // Embeddingモデル名（例：doubao-embedding-vision-251215 または text-embedding-3-large）
    },
    "max_concurrent": 10               // 最大同時Embeddingリクエスト数（デフォルト: 10）
  },
  "vlm": {
    "api_base" : "<api-endpoint>",     // APIエンドポイントアドレス
    "api_key"  : "<your-api-key>",     // モデルサービスAPIキー
    "provider" : "<provider-type>",    // プロバイダータイプ（volcengine、openai、deepseek、anthropicなど）
    "model"    : "<model-name>",       // VLMモデル名（例：doubao-seed-2-0-pro-260215 または gpt-4-vision-preview）
    "max_concurrent": 100              // セマンティック処理の最大同時LLM呼び出し数（デフォルト: 100）
  }
}
```

> **注意**: Embeddingモデルについては、現在`volcengine`（Doubao）、`openai`、`jina`プロバイダーがサポートされています。VLMモデルについては、`volcengine`と`openai`をサポートしています。

#### サーバー設定例

👇 お使いのモデルサービスの設定例を展開して確認：

<details>
<summary><b>例1: Volcengine（Doubaoモデル）を使用</b></summary>

```json
{
  "storage": {
    "workspace": "/home/your-name/openviking_workspace"
  },
  "log": {
    "level": "INFO",
    "output": "stdout"                 // ログ出力: "stdout" または "file"
  },
  "embedding": {
    "dense": {
      "api_base" : "https://ark.cn-beijing.volces.com/api/v3",
      "api_key"  : "your-volcengine-api-key",
      "provider" : "volcengine",
      "dimension": 1024,
      "model"    : "doubao-embedding-vision-251215"
    },
    "max_concurrent": 10
  },
  "vlm": {
    "api_base" : "https://ark.cn-beijing.volces.com/api/v3",
    "api_key"  : "your-volcengine-api-key",
    "provider" : "volcengine",
    "model"    : "doubao-seed-2-0-pro-260215",
    "max_concurrent": 100
  }
}
```

</details>

<details>
<summary><b>例2: OpenAIモデルを使用</b></summary>

```json
{
  "storage": {
    "workspace": "/home/your-name/openviking_workspace"
  },
  "log": {
    "level": "INFO",
    "output": "stdout"                 // ログ出力: "stdout" または "file"
  },
  "embedding": {
    "dense": {
      "api_base" : "https://api.openai.com/v1",
      "api_key"  : "your-openai-api-key",
      "provider" : "openai",
      "dimension": 3072,
      "model"    : "text-embedding-3-large"
    },
    "max_concurrent": 10
  },
  "vlm": {
    "api_base" : "https://api.openai.com/v1",
    "api_key"  : "your-openai-api-key",
    "provider" : "openai",
    "model"    : "gpt-4-vision-preview",
    "max_concurrent": 100
  }
}
```

</details>

#### サーバー設定の環境変数の設定

設定ファイルを作成後、環境変数を設定してファイルを指定します（Linux/macOS）：

```bash
export OPENVIKING_CONFIG_FILE=~/.openviking/ov.conf # デフォルト
```

Windowsの場合、以下のいずれかを使用：

PowerShell:

```powershell
$env:OPENVIKING_CONFIG_FILE = "$HOME/.openviking/ov.conf"
```

コマンドプロンプト（cmd.exe）:

```bat
set "OPENVIKING_CONFIG_FILE=%USERPROFILE%\.openviking\ov.conf"
```

> 💡 **ヒント**: 設定ファイルは他の場所に配置することもできます。環境変数で正しいパスを指定するだけです。

#### CLI/クライアント設定例

👇 CLI/クライアントの設定例を展開して確認：

例：localhostサーバー接続用のovcli.conf

```json
{
  "url": "http://localhost:1933",
  "timeout": 60.0,
  "output": "table"
}
```

設定ファイルを作成後、環境変数を設定してファイルを指定します（Linux/macOS）：

```bash
export OPENVIKING_CLI_CONFIG_FILE=~/.openviking/ovcli.conf # デフォルト
```

Windowsの場合、以下のいずれかを使用：

PowerShell:

```powershell
$env:OPENVIKING_CLI_CONFIG_FILE = "$HOME/.openviking/ovcli.conf"
```

コマンドプロンプト（cmd.exe）:

```bat
set "OPENVIKING_CLI_CONFIG_FILE=%USERPROFILE%\.openviking\ovcli.conf"
```

### 4. 最初の例を実行

> 📝 **前提条件**: 前のステップで設定（ov.confとovcli.conf）が完了していることを確認してください。

それでは、完全な例を実行してOpenVikingのコア機能を体験しましょう。

#### サーバーの起動

```bash
openviking-server
```

またはバックグラウンドで実行：

```bash
nohup openviking-server > /data/log/openviking.log 2>&1 &
```

#### CLIの実行

```bash
ov status
ov add-resource https://github.com/volcengine/OpenViking # --wait
ov ls viking://resources/
ov tree viking://resources/volcengine -L 2
# --waitを指定しない場合、セマンティック処理の完了を待つ
ov find "what is openviking"
ov grep "openviking" --uri viking://resources/volcengine/OpenViking/docs/zh
```

おめでとうございます！OpenVikingの実行に成功しました 🎉

### VikingBotクイックスタート

VikingBotは、OpenViking上に構築されたAIエージェントフレームワークです。始め方は以下の通りです：

```bash
# オプション1: PyPIからVikingBotをインストール（ほとんどのユーザーに推奨）
pip install "openviking[bot]"

# オプション2: ソースからVikingBotをインストール（開発用）
uv pip install -e ".[bot]"

# Bot有効でOpenVikingサーバーを起動
openviking-server --with-bot

# 別のターミナルでインタラクティブチャットを開始
ov chat
```

---

## サーバーデプロイの詳細

本番環境では、AIエージェントに永続的で高性能なコンテキストサポートを提供するため、OpenVikingをスタンドアロンHTTPサービスとして実行することを推奨します。

🚀 **クラウドにOpenVikingをデプロイ**:
最適なストレージパフォーマンスとデータセキュリティを確保するため、**veLinux**オペレーティングシステムを使用した**Volcengine Elastic Compute Service（ECS）**へのデプロイを推奨します。迅速に開始するための詳細なステップバイステップガイドを用意しています。

👉 **[参照: サーバーデプロイ＆ECSセットアップガイド](./docs/en/getting-started/03-quickstart-server.md)**


## OpenClawコンテキストプラグインの詳細

* テストデータセット: LoCoMo10（https://github.com/snap-research/locomo）の長距離対話に基づく効果テスト（ground truthのないcategory5を除いた合計1,540ケース）
* 実験グループ: ユーザーがOpenVikingを使用する際にOpenClawのネイティブメモリを無効にしない可能性があるため、ネイティブメモリの有効/無効の実験グループを追加
* OpenVikingバージョン: 0.1.18
* モデル: seed-2.0-code
* 評価スクリプト: https://github.com/ZaynJarvis/openclaw-eval/tree/main

| 実験グループ | タスク完了率 | コスト: 入力トークン数（合計） |
|----------|------------------|------------------|
| OpenClaw(memory-core) |	35.65% |	24,611,530 |
| OpenClaw + LanceDB (-memory-core) |	44.55% |	51,574,530 |
| OpenClaw + OpenViking Plugin (-memory-core) |	52.08% |	4,264,396 |
| OpenClaw + OpenViking Plugin (+memory-core) |	51.23% |	2,099,622 |

* 実験結果:
OpenViking統合後：
- ネイティブメモリ有効時: オリジナルOpenClawと比較して43%改善、入力トークンコスト91%削減。LanceDBと比較して15%改善、入力トークンコスト96%削減。
- ネイティブメモリ無効時: オリジナルOpenClawと比較して49%改善、入力トークンコスト83%削減。LanceDBと比較して17%改善、入力トークンコスト92%削減。

👉 **[参照: OpenClawコンテキストプラグイン](examples/openclaw-plugin/README.md)**

👉 **[参照: OpenCodeメモリプラグインの例](examples/opencode-memory-plugin/README.md)**

👉 **[参照: Claude Codeメモリプラグインの例](examples/claude-code-memory-plugin/README.md)**

--

## コアコンセプト

最初の例を実行した後、OpenVikingの設計思想を掘り下げましょう。これら5つのコアコンセプトは、先に述べたソリューションと1対1で対応し、完全なコンテキスト管理システムを構築します：

### 1. ファイルシステム管理パラダイム → 断片化の解決

コンテキストをフラットなテキストスライスとして見るのではなく、抽象的な仮想ファイルシステムに統一します。メモリ、リソース、機能のいずれも、`viking://`プロトコル下の仮想ディレクトリにマッピングされ、それぞれにユニークなURIが付与されます。

このパラダイムにより、エージェントはこれまでにないコンテキスト操作能力を獲得し、開発者のように`ls`や`find`などの標準コマンドを通じて、情報を正確かつ決定論的に位置特定、閲覧、操作できます。これにより、コンテキスト管理は曖昧なセマンティックマッチングから、直感的でトレース可能な「ファイル操作」に変わります。詳細: [Viking URI](./docs/en/concepts/04-viking-uri.md) | [コンテキストタイプ](./docs/en/concepts/02-context-types.md)

```
viking://
├── resources/              # リソース: プロジェクトドキュメント、リポジトリ、Webページなど
│   ├── my_project/
│   │   ├── docs/
│   │   │   ├── api/
│   │   │   └── tutorials/
│   │   └── src/
│   └── ...
├── user/                   # ユーザー: 個人の好み、習慣など
│   └── memories/
│       ├── preferences/
│       │   ├── writing_style
│       │   └── coding_habits
│       └── ...
└── agent/                  # エージェント: スキル、インストラクション、タスクメモリなど
    ├── skills/
    │   ├── search_code
    │   ├── analyze_data
    │   └── ...
    ├── memories/
    └── instructions/
```

### 2. 階層型コンテキストローディング → トークン消費の削減

大量のコンテキストをプロンプトに一度に詰め込むことは、コストが高いだけでなく、モデルウィンドウの超過やノイズの混入を招きやすいです。OpenVikingは書き込み時にコンテキストを自動的に3つのレベルに処理します：
- **L0（Abstract）**: 迅速な検索と識別のための一文の要約。
- **L1（Overview）**: 計画フェーズでのエージェントの意思決定のための、コア情報と使用シナリオを含む。
- **L2（Details）**: エージェントが絶対に必要な場合の深い読み込みのための、完全なオリジナルデータ。

詳細: [コンテキストレイヤー](./docs/en/concepts/03-context-layers.md)

```
viking://resources/my_project/
├── .abstract               # L0レイヤー: 要約（〜100トークン）- 迅速な関連性チェック
├── .overview               # L1レイヤー: 概要（〜2kトークン）- 構造とキーポイントの理解
├── docs/
│   ├── .abstract          # 各ディレクトリに対応するL0/L1レイヤーあり
│   ├── .overview
│   ├── api/
│   │   ├── .abstract
│   │   ├── .overview
│   │   ├── auth.md        # L2レイヤー: 完全なコンテンツ - オンデマンドでロード
│   │   └── endpoints.md
│   └── ...
└── src/
    └── ...
```

### 3. ディレクトリ再帰検索 → 検索効果の向上

単一のベクトル検索では、複雑なクエリインテントへの対応が困難です。OpenVikingは、複数の検索手法を深く統合する革新的な**ディレクトリ再帰検索戦略**を設計しました：

1. **インテント分析**: インテント分析により複数の検索条件を生成。
2. **初期位置特定**: ベクトル検索を使用して、初期スライスが位置する高スコアディレクトリを素早く特定。
3. **詳細な探索**: そのディレクトリ内で二次検索を実行し、高スコア結果を候補セットに更新。
4. **再帰的掘り下げ**: サブディレクトリが存在する場合、二次検索ステップを層ごとに再帰的に繰り返し。
5. **結果集約**: 最終的に、最も関連性の高いコンテキストを取得して返却。

この「まず高スコアディレクトリを特定し、次にコンテンツ探索を精緻化する」戦略は、セマンティック的に最もマッチするフラグメントを見つけるだけでなく、情報が存在するコンテキスト全体を理解し、検索のグローバル性と精度を向上させます。詳細: [検索メカニズム](./docs/en/concepts/07-retrieval.md)

### 4. 可視化された検索軌跡 → 観察可能なコンテキスト

OpenVikingの組織は階層的な仮想ファイルシステム構造を使用しています。すべてのコンテキストは統一されたフォーマットで統合され、各エントリはユニークなURI（`viking://`パスのようなもの）に対応し、従来のフラットなブラックボックス管理モードを、理解しやすい明確な階層で打ち破ります。

検索プロセスはディレクトリ再帰戦略を採用しています。各検索のディレクトリブラウジングとファイル位置特定の軌跡が完全に保存され、ユーザーが問題の根本原因を明確に観察し、検索ロジックの最適化を導くことを可能にします。詳細: [検索メカニズム](./docs/en/concepts/07-retrieval.md)

### 5. 自動セッション管理 → コンテキストの自己反復

OpenVikingにはメモリ自己反復ループが組み込まれています。各セッションの終了時に、開発者はメモリ抽出メカニズムを能動的にトリガーできます。システムはタスク実行結果とユーザーフィードバックを非同期的に分析し、ユーザーとエージェントのメモリディレクトリに自動的に更新します。

- **ユーザーメモリの更新**: ユーザーの好みに関するメモリを更新し、エージェントの応答がユーザーのニーズにより適合するように。
- **エージェントの経験蓄積**: タスク実行経験から操作のヒントやツールの使用経験などのコアコンテンツを抽出し、後続タスクでの効率的な意思決定を支援。

これにより、エージェントは世界とのインタラクションを通じて「使うほど賢く」なり、自己進化を実現します。詳細: [セッション管理](./docs/en/concepts/08-session.md)

---

## 上級者向け資料

### ドキュメント

詳細については、[完全なドキュメント](./docs/en/)をご覧ください。

### コミュニティとチーム

詳細については、**[私たちについて](./docs/en/about/01-about-us.md)**をご覧ください。

### コミュニティに参加

OpenVikingはまだ初期段階にあり、改善と探索の余地が多くあります。AIエージェント技術に情熱を持つすべての開発者を心から招待します：

- 前進の原動力となる貴重な**Star**をお願いします。
- 私たちの[**Webサイト**](https://www.openviking.ai)を訪れて、伝えたい思想を理解し、[**ドキュメント**](https://www.openviking.ai/docs)を通じてプロジェクトで使用してください。変化を感じ、最も率直な体験をフィードバックしてください。
- コミュニティに参加して、洞察を共有し、他の人の質問に答え、オープンで互助的な技術の雰囲気を共に作りましょう：
  - 📱 **Larkグループ**: QRコードをスキャンして参加 → [QRコードを表示](./docs/en/about/01-about-us.md#lark-group)
  - 💬 **WeChatグループ**: QRコードをスキャンしてアシスタントを追加 → [QRコードを表示](./docs/en/about/01-about-us.md#wechat-group)
  - 🎮 **Discord**: [Discordサーバーに参加](https://discord.com/invite/eHvx8E9XF3)
  - 🐦 **X（Twitter）**: [フォローする](https://x.com/openvikingai)
- **コントリビューター**になってください。バグ修正の提出でも新機能のコントリビューションでも、あなたのコードの一行一行がOpenVikingの成長の重要な礎石となります。

AIエージェントのコンテキスト管理の未来を共に定義し、構築しましょう。旅は始まりました。あなたの参加をお待ちしています！

### Starの推移

[![Star History Chart](https://api.star-history.com/svg?repos=volcengine/OpenViking&type=timeline&legend=top-left)](https://www.star-history.com/#volcengine/OpenViking&type=timeline&legend=top-left)

## ライセンス

OpenVikingプロジェクトは、コンポーネントごとに異なるライセンスを使用しています：

- **メインプロジェクト**: AGPLv3 - 詳細は[LICENSE](./LICENSE)ファイルを参照してください
- **crates/ov_cli**: Apache 2.0 - 詳細は[LICENSE](./crates/ov_cli/LICENSE)ファイルを参照してください
- **examples**: Apache 2.0 - 詳細は[LICENSE](./examples/LICENSE)ファイルを参照してください
- **third_party**: 各サードパーティプロジェクトの元のライセンス


<!-- リンク定義 -->

[release-shield]: https://img.shields.io/github/v/release/volcengine/OpenViking?color=369eff&labelColor=black&logo=github&style=flat-square
[release-link]: https://github.com/volcengine/OpenViking/releases
[license-shield]: https://img.shields.io/badge/license-AGPLv3-white?labelColor=black&style=flat-square
[license-shield-link]: https://github.com/volcengine/OpenViking/blob/main/LICENSE
[last-commit-shield]: https://img.shields.io/github/last-commit/volcengine/OpenViking?color=c4f042&labelColor=black&style=flat-square
[last-commit-shield-link]: https://github.com/volcengine/OpenViking/commits/main
[github-stars-shield]: https://img.shields.io/github/stars/volcengine/OpenViking?labelColor&style=flat-square&color=ffcb47
[github-stars-link]: https://github.com/volcengine/OpenViking
[github-issues-shield]: https://img.shields.io/github/issues/volcengine/OpenViking?labelColor=black&style=flat-square&color=ff80eb
[github-issues-shield-link]: https://github.com/volcengine/OpenViking/issues
[github-contributors-shield]: https://img.shields.io/github/contributors/volcengine/OpenViking?color=c4f042&labelColor=black&style=flat-square
[github-contributors-link]: https://github.com/volcengine/OpenViking/graphs/contributors
