<div align="center">

<a href="https://openviking.ai/" target="_blank">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/volcengine/OpenViking/main/docs/images/readme-logo-dark.png">
    <img alt="OpenViking" src="https://raw.githubusercontent.com/volcengine/OpenViking/main/docs/images/readme-logo-light.png" width="300" height="56">
  </picture>
</a>

### AIエージェントのためのコンテキストデータベース

[English](../../README.md) / [中文](README_CN.md) / 日本語

<a href="https://www.openviking.ai">Webサイト</a> · <a href="https://openviking.ai/studio">ライブデモ</a> · <a href="https://github.com/volcengine/OpenViking">GitHub</a> · <a href="https://github.com/volcengine/OpenViking/issues">Issues</a> · <a href="https://docs.openviking.ai/">ドキュメント</a> · <a href="https://blog.openviking.ai/">ブログ</a>

<p>
  <a href="https://github.com/volcengine/OpenViking/releases"><img src="https://img.shields.io/github/v/release/volcengine/OpenViking?color=369eff&labelColor=black&logo=github&style=flat-square" alt="release"></a>
  <a href="https://github.com/volcengine/OpenViking"><img src="https://img.shields.io/github/stars/volcengine/OpenViking?labelColor&style=flat-square&color=ffcb47" alt="stars"></a>
  <a href="https://github.com/volcengine/OpenViking/issues"><img src="https://img.shields.io/github/issues/volcengine/OpenViking?labelColor=black&style=flat-square&color=ff80eb" alt="issues"></a>
  <a href="https://github.com/volcengine/OpenViking/graphs/contributors"><img src="https://img.shields.io/github/contributors/volcengine/OpenViking?color=c4f042&labelColor=black&style=flat-square" alt="contributors"></a>
  <a href="https://github.com/volcengine/OpenViking/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-AGPLv3-white?labelColor=black&style=flat-square" alt="license"></a>
  <a href="https://github.com/volcengine/OpenViking/commits/main"><img src="https://img.shields.io/github/last-commit/volcengine/OpenViking?color=c4f042&labelColor=black&style=flat-square" alt="last commit"></a>
</p>

<p>
  <a href="https://railway.com/deploy/openviking"><img src="https://railway.com/button.svg" alt="Deploy on Railway" height="30"></a>
</p>

<p>
  <a href="https://docs.openviking.ai/en/about/01-about-us#lark-group"><img src="../images/community/lark.svg" width="18" height="18" alt="Lark">&nbsp;Lark</a> ·
  <a href="https://docs.openviking.ai/en/about/01-about-us#wechat-group"><img src="../images/community/wechat.svg" width="18" height="18" alt="WeChat">&nbsp;WeChat</a> ·
  <a href="https://discord.com/invite/eHvx8E9XF3"><img src="../images/community/discord.svg" width="18" height="18" alt="Discord">&nbsp;Discord</a> ·
  <a href="https://x.com/openvikingai"><picture><source media="(prefers-color-scheme: dark)" srcset="../images/community/x-dark.svg"><img src="../images/community/x.svg" width="16" height="16" alt="X"></picture>&nbsp;X</a>
</p>

<a href="https://trendshift.io/repositories/19668" target="_blank"><img src="https://trendshift.io/api/badge/repositories/19668" alt="volcengine%2FOpenViking | Trendshift" style="width: 250px; height: 55px;" width="250" height="55"/></a>

</div>

***

## OpenVikingとは

OpenVikingは、AIエージェントのためのオープンソースのコンテキストデータベースです。知識・記憶・スキル——エージェントが知っているすべてを、一つのファイルシステムにまとめます。

多くのエージェントメモリはブラックボックスです。テキストを入れると埋め込みが返ってきますが、何が保存されたのかは誰にも見えません。OpenVikingは、コンテキストを `viking://` という仮想ファイルシステムとして整理します。エージェントは `ls`、`tree`、`read`、`write`、`grep` でファイルのように操作し、人間もディレクトリを開いて中身を確認・編集できます。各ディレクトリには自動生成の要約があり、エージェントは要約を確認してから読む内容を決められます。

<a href="https://openviking.ai/studio" target="_blank" rel="noopener noreferrer">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="../images/studio-playground-dark.png">
    <img src="../images/studio-playground.png" alt="OpenViking Studio：コンテキストの閲覧と意味検索">
  </picture>
</a>

[OpenViking Studioを試す](https://openviking.ai/studio)。ブラウザから利用でき、インストールは不要です。 [Web Studioを自分の環境にデプロイ](../../web-studio/README.md)。

## OpenVikingを選ぶ理由

- **知識・記憶・スキルを一つのファイルシステムに。** リソースは文書やコード、メモリはユーザーの好みや経験、スキルはタスクの実行方法を保存します。抽出された事実だけでなく完全なコンテキストが、それぞれ `viking://` URI を持ち、閲覧・検索できます。→ [Viking URI](https://docs.openviking.ai/en/concepts/04-viking-uri) · [Context types](https://docs.openviking.ai/en/concepts/02-context-types)
- **インデックス全体ではなく、ディレクトリを検索。** プロジェクトやメモリのサブツリーに意味検索の範囲を絞り、フラットなベクトルプールをスキャンしません。`find` はクエリを直接実行し、`search` はセッションのコンテキストから検索を計画します。→ [Retrieval](https://docs.openviking.ai/en/concepts/07-retrieval)
- **全文の前に、まず要約を読む。** 自動生成されるディレクトリの要約（L0）と概要（L1）で関連性を判断してから、全文（L2）を開きます。→ [Context layers](https://docs.openviking.ai/en/concepts/03-context-layers)
- **セッションは読めるファイルになる。** コミットすると会話をアーカイブし、メモリを確認・編集・統合できる Markdown として抽出します。VikingBot を有効にすると、`ov compile` で資料を Wiki、知識グラフ、レポートに整理できます。→ [Session](https://docs.openviking.ai/en/concepts/08-session) · [Context compilation](https://docs.openviking.ai/en/context-compilation/01-overview)

[Architecture](https://docs.openviking.ai/en/concepts/01-architecture) · [設計の背景](https://blog.openviking.ai/post/openviking-context-database/)

```
viking://
├── resources/              # リソース: プロジェクトドキュメント、リポジトリ、Webページなど
│   └── my_project/
│       ├── docs/
│       │   ├── api/
│       │   └── tutorials/
│       └── src/
└── user/
    └── {user_id}/
        ├── memories/
        │   └── preferences/
        │       ├── writing_style
        │       └── coding_habits
        ├── resources/
        │   └── private_project/
        ├── skills/
        │   ├── search_code
        │   └── analyze_data
        └── peers/
            └── web-visitor-alice/
```

3つのローディング階層:

- **L0（Abstract）**: 迅速な関連性チェックのための一文の要約。
- **L1（Overview）**: 計画立案のためのコア情報と使用シナリオ。
- **L2（Details）**: 完全なオリジナルデータ。必要な場合にのみ読み込まれます。

意味処理を終えたディレクトリには L0/L1 の要約があり、全文を読む前に関連性を判断できます。

```
viking://resources/my_project/
├── .abstract.md           # L0: 〜100 tokens - 迅速な関連性チェック
├── .overview.md           # L1: 〜2k tokens - 構造とキーポイント
└── docs/
    ├── .abstract.md
    ├── .overview.md
    └── api/
        ├── auth.md         # L2: 完全なコンテンツ、オンデマンドでロード
        └── endpoints.md
```

## 実証データ

OpenViking 0.3.22 は、長い会話でのユーザーメモリ（LoCoMo）と複数ターンのエージェントタスク（tau2-bench）で評価されています。ナレッジベースQAを含む完全な結果と実験設定は[ベンチマークレポート](https://blog.openviking.ai/post/openviking-benchmark-results/)を、再現用スクリプトは [./benchmark](../../benchmark) を参照してください。

メモリ評価では、VLM に [Doubao 2.0 Pro](https://console.volcengine.com/ark/region:cn-beijing/model/detail?Id=doubao-seed-2-0-pro)、Embedding モデルに [Doubao-embedding-vision-251215](https://console.volcengine.com/ark/region:cn-beijing/model/detail?Id=doubao-embedding-vision) を使用しました。

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="../images/benchmark-dark.svg">
  <img alt="Benchmark results. LoCoMo accuracy: OpenClaw 24.20% native vs 82.08% with OpenViking; Hermes 33.38% vs 82.86%; Claude Code 57.21% vs 80.32%. tau2-bench task success: Retail 70.94% vs 77.81%; Airline 54.38% vs 66.25%." src="../images/benchmark-light.svg">
</picture>

- **ユーザーメモリ（LoCoMo）**: OpenViking を接続すると、3つのエージェント統合すべてで精度が 80–83% に達します（ネイティブメモリでは 24–57%）。同時に入力 token は 34.3–91.0%、クエリレイテンシは 58.45–66.10% 削減されます。
- **エージェント経験（tau2-bench)**: 経験メモリにより、タスク成功率は同一 LLM（メモリなし）比で Retail +6.87pp、Airline +11.87pp 向上します。

## クイックスタート

まず OpenViking サーバーを用意します。すでにある場合は[エージェントと組み合わせて使う](#エージェントと組み合わせて使う)に進んでください。自前でデプロイするには、uv、Python 3.10+、そして embedding モデルと VLM を提供するモデルプロバイダーが必要です。

<details open>
<summary><strong>エージェントにデプロイしてもらう</strong></summary>

```text
このガイドに従って、OpenViking Server をインストールして起動すること：

https://docs.openviking.ai/en/getting-started/04-setup-for-agent

モデルプロバイダー、モデル、workspace ディレクトリ、
ほかのマシンからサーバーに接続する必要があるかは、
推測せずに私に確認すること。モデルの API キーを求めるときは、
このチャットに貼りたくない場合の渡し方も伝えること。キーは復唱しないこと。

起動したら、サーバーのアドレスと、認証が有効かどうかを伝えること。
```

エージェントは最初に、どのモデルプロバイダーを使うかとその API キーを尋ねます。

</details>

<details>
<summary><strong>自分でデプロイ</strong></summary>

OpenViking をインストールしてセットアップウィザードを実行し、モデルを設定します：

```bash
uv tool install openviking --upgrade && openviking-server init
```

`init` は `~/.openviking/ov.conf` に設定を書き込みます。Volcengine、OpenAI、Codex OAuth、Kimi、GLM、ローカルの Ollama などに対応しています。詳しくは[設定ガイド](https://docs.openviking.ai/en/guides/01-configuration)を参照してください。サーバーはフォアグラウンドで動くので、このターミナルは閉じないでください。

</details>

<details>
<summary><strong>OpenViking Service を使う（Volcengine がホスト）</strong></summary>

同じ OpenViking サービスを Volcengine が代わりに運用します。最初の 50 ファイルは無料です。[Volcengine の製品ページ](https://www.volcengine.com/product/openviking-service?utm_source=github&utm_medium=referral&utm_campaign=readme)で利用を開始したら、コンソールの「User Management → API Key」で API キーを作成してください。サーバーのアドレスは `https://api.vikingdb.cn-beijing.volces.com/openviking` です。エージェントを接続するときに、このアドレスと API キーを使います。

</details>

### CLI で試す

`openviking` パッケージには `ov` CLI が含まれます。サーバーが動いている状態で、リポジトリを取り込み、検索します。

```bash
ov status
ov add-resource https://github.com/volcengine/OpenViking
# TASK_ID を返された task_id に置き換え、status が completed になるまで確認
ov task status TASK_ID
ov ls viking://resources/
ov tree viking://resources/volcengine -L 2
ov find "what is openviking"
ov grep "openviking" --uri viking://resources/volcengine/OpenViking/docs/en
```

`ov find` は一致したコンテキストと URI を返します。クライアント設定（`ov config`）、CLI の単体インストール、インデックス管理は [CLI セットアップ](https://docs.openviking.ai/en/getting-started/05-cli-setup)を参照してください。

独自のアプリには [Python](../../sdk/python/README.md)、[Go](../../sdk/go/README.md)、[TypeScript](../../sdk/typescript/README.md) SDK、または [HTTP API](https://docs.openviking.ai/en/api/01-overview) を使えます。

## エージェントと組み合わせて使う

コーディングエージェントを OpenViking に接続して、セッションをまたいで記憶を引き継ぎます。メモリプラグインのインストーラーは Claude Code、Codex、Cursor、TRAE、OpenCode などに対応し、入っているツールを自動で検出します。

<details open>
<summary><strong>エージェントにインストールしてもらう</strong></summary>

```text
OpenViking メモリプラグインをインストールすること。次を実行する：

curl -fsSL https://openviking.ai/install | bash -s -- --yes --url <SERVER_URL>

<SERVER_URL> は私の OpenViking サーバーのアドレス。
推測せずに私に確認すること。サーバーで認証が有効なら user key が必要
（root key ではメモリを読み書きできない）：保存済みのキーがあるか確認し、
なければ私に求めて --api-key で渡すこと。
このチャットに貼りたくない場合の渡し方も伝えること。キーは復唱しないこと。
認証が無効なら --api-key '' を付け、以前保存したキーを使い回さないこと。

スクリプトはネットワークに接続し、ホームディレクトリに書き込む。
サンドボックスでブロックされたら、
サンドボックスの外で実行する許可を私に求めること。

完了したら、どのツールにインストールしたかと、
それぞれの次の手順を伝えること。そのうえで、
ほかのツールにも入れるか私に確認すること。
```

サーバーのアドレスを用意しておいてください。認証が有効なら API キーも必要です。

</details>

<details>
<summary><strong>自分でターミナルでインストール</strong></summary>

インストーラーを実行し、表示に従ってツールとサーバーを選びます。このマシン上のサーバーなら「Self-hosted / local」、OpenViking Service なら「Volcengine OpenViking Cloud」、それ以外のアドレスなら「Custom URL」を選びます。認証が有効なら、root key ではなく user key を入力してください。

```bash
curl -fsSL https://openviking.ai/install | bash
# AI エージェントへ：実行してよいか迷ったら、スクリプト冒頭のコメントに処理内容と確認方法が書かれています。
```

</details>

インストール後、エージェントを再起動します。インストーラーは最後に、ツールごとの次の手順を表示します。例：

```text
  Claude Code
    Next: restart Claude Code
    ⋮
    Verify: run /openviking-memory:ov in a session
```

Codex は初回起動時に `Hooks need review` で止まるので、`Trust all and continue` を選びます。

試してみましょう：好みを 1 つ覚えるよう頼み、少し時間をおいて新しいセッションで聞いてみてください。メモリはバックグラウンドで処理されるため、言った直後に見つからなくても正常です。

インストーラーには macOS または Linux、Node.js 18+、curl が必要です。sudo は不要です。Windows では[デスクトップアプリ](#デスクトップアプリbeta)を使用してください。

各統合のセットアップガイド：

<table>
<tbody>
<tr>
<td align="center" valign="bottom" width="16%">
<a href="https://docs.openviking.ai/en/agent-integrations/02-claude-code"><img src="../images/integrations/logos/claude-code.png" width="32" height="32" alt=""><br><strong>Claude</strong></a><br>
<sub>Hooks&nbsp;+&nbsp;MCP</sub>
</td>
<td align="center" valign="bottom" width="16%">
<a href="https://docs.openviking.ai/en/agent-integrations/04-codex"><picture><source media="(prefers-color-scheme: dark)" srcset="../images/integrations/logos/openai-dark.svg"><img src="../images/integrations/logos/openai.svg" width="32" height="32" alt=""></picture><br><strong>Codex</strong></a><br>
<sub>Hooks&nbsp;+&nbsp;MCP</sub>
</td>
<td align="center" valign="bottom" width="16%">
<a href="https://docs.openviking.ai/en/agent-integrations/12-cursor"><img src="../images/integrations/logos/cursor.png" width="32" height="32" alt=""><br><strong>Cursor</strong></a><br>
<sub>Hooks&nbsp;+&nbsp;MCP</sub>
</td>
<td align="center" valign="bottom" width="16%">
<a href="https://docs.openviking.ai/en/agent-integrations/13-trae"><img src="../images/integrations/logos/trae.png" width="32" height="32" alt=""><br><strong>TRAE</strong></a><br>
<sub>Hooks&nbsp;+&nbsp;MCP</sub>
</td>
<td align="center" valign="bottom" width="16%">
<a href="https://docs.openviking.ai/en/agent-integrations/03-openclaw"><img src="../images/integrations/logos/openclaw.png" width="32" height="32" alt=""><br><strong>OpenClaw</strong></a><br>
<sub>コンテキストエンジン</sub>
</td>
<td align="center" valign="bottom" width="16%">
<a href="https://docs.openviking.ai/en/agent-integrations/05-hermes"><img src="../images/integrations/logos/hermes-agent.png" width="32" height="32" alt=""><br><strong>Hermes</strong></a><br>
<sub>内蔵メモリ</sub>
</td>
</tr>
</tbody>
<tbody>
<tr>
<td align="center" valign="bottom" width="16%">
<a href="https://docs.openviking.ai/en/agent-integrations/10-opencode"><img src="../images/integrations/logos/opencode.png" width="32" height="32" alt=""><br><strong>OpenCode</strong></a><br>
<sub>Plugin&nbsp;+&nbsp;MCP</sub>
</td>
<td align="center" valign="bottom" width="16%">
<a href="https://docs.openviking.ai/en/agent-integrations/11-pi"><picture><source media="(prefers-color-scheme: dark)" srcset="../images/integrations/logos/pi-dark.svg"><img src="../images/integrations/logos/pi.svg" width="32" height="32" alt=""></picture><br><strong>pi</strong></a><br>
<sub>ネイティブ拡張</sub>
</td>
<td align="center" valign="bottom" width="16%">
<a href="../images/agents/en/deerflow-memory-manager.md"><picture><source media="(prefers-color-scheme: dark)" srcset="../images/integrations/logos/deerflow-dark.svg"><img src="../images/integrations/logos/deerflow.svg" width="32" height="32" alt=""></picture><br><strong>DeerFlow</strong></a><br>
<sub>Plugin&nbsp;+&nbsp;MCP</sub>
</td>
<td align="center" valign="bottom" width="16%">
<a href="https://docs.openviking.ai/en/agent-integrations/17-dsh"><picture><source media="(prefers-color-scheme: dark)" srcset="../images/integrations/logos/dsh-dark.svg"><img src="../images/integrations/logos/dsh.svg" width="32" height="32" alt=""></picture><br><strong>DSH</strong></a><br>
<sub>Plugin&nbsp;+&nbsp;MCP</sub>
</td>
<td align="center" valign="bottom" width="16%">
<a href="../images/agents/en/doubao-work.md"><img src="../images/integrations/logos/doubao-work.png" width="32" height="32" alt=""><br><strong>Doubao&nbsp;Work</strong></a><br>
<sub>コネクタ</sub>
</td>
<td align="center" valign="bottom" width="16%">
<a href="https://docs.openviking.ai/en/agent-integrations/07-langchain-langgraph"><img src="../images/integrations/logos/langchain.svg" width="32" height="32" alt=""><br><strong>LangChain</strong></a><br>
<sub>ツール&nbsp;+&nbsp;ストア</sub>
</td>
</tr>
</tbody>
</table>

**汎用接続**

<table>
<tr>
<td align="center" valign="bottom" width="50%">
<a href="https://docs.openviking.ai/en/agent-integrations/15-agent-plugins"><img src="../images/integrations/logos/agent-plugins.svg" width="32" height="32" alt=""><br><strong>Agent&nbsp;Plugins&nbsp;1.0</strong></a>
</td>
<td align="center" valign="bottom" width="50%">
<a href="https://docs.openviking.ai/en/agent-integrations/06-mcp-clients"><img src="../images/integrations/logos/mcp.svg" width="32" height="32" alt=""><br><strong>MCP&nbsp;ク&#8288;ラ&#8288;イ&#8288;ア&#8288;ン&#8288;ト</strong></a>
</td>
</tr>
</table>

設定方法と統合の詳細は [Integrations](https://openviking.ai/integrations) を参照してください。

## デスクトップアプリ（Beta）

デスクトップアプリは macOS と Windows x64 向けのコンソール（Beta）です。対応するローカルエージェントとの連携を設定し、セッションのリコールやキャプチャを確認して、ローカルのメモリとスキルを OpenViking に同期できます。

ダウンロード:

- [macOS Apple Silicon (arm64)](https://lf3-cdn-tos.bytegoofy.com/obj/tron-demo/7654844610543360265/420238785/0.0.19/darwin-arm64/openviking-helper-0.0.19-arm64.dmg)
- [macOS Intel (x64)](https://lf3-cdn-tos.bytegoofy.com/obj/tron-demo/7654844610543360265/420238785/0.0.19/darwin-x64/openviking-helper-0.0.19-x64.dmg)
- [Windows (x64)](https://lf3-cdn-tos.bytegoofy.com/obj/tron-demo/7654844610543360265/420238785/0.0.19/win32-x64/openviking-helper-0.0.19-x64.exe)

## VikingBot

VikingBot は、OpenViking 上に構築された AI エージェントフレームワークです:

```bash
pip install "openviking[bot]"
openviking-server --with-bot
ov chat   # 別のターミナルで実行
```

公式 Docker イメージには VikingBot が同梱されており、サーバーとコンソール UI とともにデフォルトで起動します。詳細: [VikingBot guide](https://docs.openviking.ai/en/guides/17-vikingbot)。

## 本番環境へのデプロイ

オープンソースのサーバーは [AGPLv3](../../LICENSE) のもとで自分の環境にデプロイでき、アクティベーションキーは不要です。[サーバー設定](https://docs.openviking.ai/en/getting-started/03-quickstart-server) · [Docker とデプロイのガイド](https://docs.openviking.ai/en/guides/03-deployment)

サーバーは[アカウントとユーザーの分離](https://docs.openviking.ai/en/concepts/11-multi-tenant)に対応し、[リソース ACL](https://docs.openviking.ai/en/concepts/15-acl)を必要に応じて有効にできます。localhost 以外から接続する前に[認証](https://docs.openviking.ai/en/guides/04-authentication)を設定してください。

## 商用版

<table>
<tr>
<td width="50%" valign="top">

<img src="../images/commercial-saas.png" alt="マネージド SaaS 版" width="100%" />

<h3>☁️ マネージド SaaS 版</h3>
<p><a href="https://www.volcengine.com/product/openviking-service?utm_source=github&amp;utm_medium=referral&amp;utm_campaign=readme">Volcano Engine</a> がホスティングと運用を担当します。個人向けと企業向けのプラン、オープンソース環境からの移行ツールを提供します。プランと制限は<a href="https://docs.volcengine.com/docs/84313/2374478">サービス文書</a>を参照してください。中国以外でのホスティングは <a href="https://www.byteplus.com">BytePlus</a> で予定されています。</p>

</td>
<td width="50%" valign="top">

<img src="../images/commercial-self-hosted.png" alt="プライベートデプロイ版" width="100%" />

<h3>🏢 プライベートデプロイ版</h3>
<p>自社のクラウドアカウント / VPC（BYOC）、またはオフライン環境にデプロイできます。分散デプロイと公式サポートを提供し、ライセンスキーで有効化します。<a href="https://docs.google.com/forms/d/e/1FAIpQLScQqwsm7fvKdjtNiW5rWNXJjoHPtedVzLsKSMJgObtsj2_udA/viewform">チームに問い合わせる</a>。</p>

</td>
</tr>
</table>

## 研究

**対話とともに進化するエージェントの記憶。** VikingMem は、イベントを起点に長期記憶を抽出・更新・統合し、状態を持つエージェントが対話を通じて再利用できる経験を蓄積する仕組みを示しています。OpenViking は、そのコア機能の一部をオープンソースとして公開しています。

> **VikingMem: A Memory Base Management System for Stateful LLM-based Applications**<br>
> Jiajie Fu, Junwen Chen, Mengzhao Wang, Aoxiang He, Maojia Sheng, Xiangyu Ke, Yifan Zhu, and Yunjun Gao.<br>
> arXiv:2605.29640, 2026. 2026 年 9 月に VLDB 2026 で発表済み。<br>
> 📄 [arXiv で論文を読む](https://arxiv.org/abs/2605.29640) · [PDF を読む](https://arxiv.org/pdf/2605.29640)

**ディレクトリ構造を検索のコンテキストに。** 本論文は、OpenViking のディレクトリを考慮した検索に形式的基盤、インデックス設計、実験による検証を提供します。ディレクトリ範囲のクエリと構造の保守操作を定義し、TrieHI を提案しています。OpenViking はこれを統合し、ベクトルによる順位付けの前に検索範囲を確定します。エージェントはプロジェクトや記憶のサブツリー内で根拠を探し、周辺のコンテキストを保ちながら、知識の変化に応じてディレクトリを再編できます。

> **Directory-Aware Query and Maintenance in Vector Databases**<br>
> Mengzhao Wang, Zheng Gong, Jingpei Hu, Jiajie Fu, Maojia Sheng, Junwen Chen, and Yifan Zhu.<br>
> arXiv:2606.16903, 2026. ICDE 採択済み。<br>
> 📄 [arXiv で論文を読む](https://arxiv.org/abs/2606.16903) · [PDF を読む](https://arxiv.org/pdf/2606.16903)

**少ないトークンで回答に必要な根拠を集める。** VikingRAG は意味検索と文書構造を組み合わせ、根拠の不足に応じて関連するディレクトリ部分を取得します。そのコア機構は OpenViking に統合されています。さらに、検索履歴の再利用と必要な場合のみ複数ラウンドの検索へ移行する手法を研究し、回答品質を保ちながら探索の繰り返しを減らします。

> **VikingRAG: Accurate and Token-efficient Retrieval-augmented Generation over Structured Documents**<br>
> Peiyuan Gao, Gaoyuan Zhang, Haojie Qin, Yahui Sun, Qianyi Zhang, Yunhao Zhang, Zeyu Wang, and Wei Lu.<br>
> arXiv:2609.11390, 2026. 投稿中。<br>
> 📄 [arXiv で論文を読む](https://arxiv.org/abs/2609.11390) · [PDF を読む](https://arxiv.org/pdf/2609.11390)

## パートナープロジェクト

- [deer-flow](https://github.com/bytedance/deer-flow) - オープンソースの長時間 SuperAgent フレームワーク
- [NoKV](https://github.com/NoKV-Lab/NoKV) - AI ネイティブの分散ファイルシステム
- [loopx](https://github.com/huangruiteng/loopx) - 軽量なループエンジニアリング状態カーネル
- [Hermes Agent](https://github.com/NousResearch/hermes-agent) - あなたと共に成長するエージェント

提携の提案は [issue](https://github.com/volcengine/OpenViking/issues) で受け付けています。

## コミュニティとコントリビューション

- **ドキュメント**: [docs.openviking.ai](https://docs.openviking.ai/) · [FAQ](https://docs.openviking.ai/en/faq/faq)
- **ブログ**: [blog.openviking.ai](https://blog.openviking.ai/)
- **チーム**: [About us](https://docs.openviking.ai/en/about/01-about-us)
- **チャット**: <a href="https://docs.openviking.ai/en/about/01-about-us#lark-group"><img src="../images/community/lark.svg" width="18" height="18" alt="Lark">&nbsp;Lark</a> · <a href="https://docs.openviking.ai/en/about/01-about-us#wechat-group"><img src="../images/community/wechat.svg" width="18" height="18" alt="WeChat">&nbsp;WeChat</a> · <a href="https://discord.com/invite/eHvx8E9XF3"><img src="../images/community/discord.svg" width="18" height="18" alt="Discord">&nbsp;Discord</a> · <a href="https://x.com/openvikingai"><picture><source media="(prefers-color-scheme: dark)" srcset="../images/community/x-dark.svg"><img src="../images/community/x.svg" width="16" height="16" alt="X"></picture>&nbsp;X</a>
- **コントリビュート**: バグ修正も新機能も歓迎します — [CONTRIBUTING_JA.md](CONTRIBUTING_JA.md) を参照してください

<a href="https://github.com/volcengine/OpenViking/graphs/contributors">
  <img src="https://contrib.rocks/image?repo=volcengine/OpenViking&amp;columns=15&amp;max=120" alt="OpenViking contributors" />
</a>

## セキュリティとプライバシー

脆弱性の報告方法とサポート対象バージョンについては、[SECURITY.md](../../SECURITY.md) を参照してください

## ライセンス

OpenViking プロジェクトは、コンポーネントごとに異なるライセンスを使用しています:

- **メインプロジェクト**: AGPLv3 - 詳細は [LICENSE](../../LICENSE) ファイルを参照してください
- **crates/ov\_cli**: Apache 2.0 - 詳細は [LICENSE](../../crates/LICENSE) を参照してください
- **examples**: Apache 2.0 - 詳細は [LICENSE](../../examples/LICENSE) を参照してください。`examples/hermes-plugin` の Hermes プラグインは元の [MIT ライセンス](../../examples/hermes-plugin/LICENSE) を保持します。
- **third\_party**: 各サードパーティプロジェクトの元のライセンス
