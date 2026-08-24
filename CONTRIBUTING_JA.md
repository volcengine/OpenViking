# OpenVikingへのコントリビューション

[English](CONTRIBUTING.md) / [中文](CONTRIBUTING_CN.md) / 日本語

OpenVikingへのコントリビューションありがとうございます。このガイドは、明確で
焦点が絞られ、レビューしやすい変更を提出するためのものです。

バグ報告、機能リクエスト、ドキュメント改善、コードのコントリビューションを歓迎します。

## 重視すること

OpenVikingでは、焦点が絞られ、十分に理解された変更を重視します。AIツールの使用有無に
かかわらず、コントリビューターは自分の変更を理解し、説明し、検証する責任を負います。

必要十分な最小の変更を優先してください。簡潔なコードとは、必要な行数を削ることではなく、
概念、分岐、重複ルール、推測に基づく抽象化を減らすことです。良い変更は直接的で読みやすく、
エントリーポイントから観測可能な動作まで説明できます。

具体的には：

- 1つのPRでは、まとまりのある1つの問題だけを解決してください。無関係な整理や
  リファクタリングを混ぜないでください。
- ルールを所有する既存のモジュールを再利用し、並行する仕組みを追加しないでください。
- 推測に基づくフォールバック、フラグ、状態フィールド、抽象化を避けてください。
- 新しい実装によって置き換えられたコード、テスト、互換パスは削除してください。
- 所有権、ライフサイクル、失敗処理を明確にするために必要な構造は残してください。

### レビューの優先度

メンテナーの時間には限りがあるため、焦点を絞ったPRからレビューします：

- **変更行数が100行以下**のPRは、通常より迅速に確認されます。
- **変更行数が200行以下**のPRは、それより大きなPRより優先して確認されます。

これはレビューの優先度であり、厳格な上限や応答時間の保証ではありません。変更行数は、
手書きのソース、テスト、ドキュメントにおける追加行と削除行の合計です。生成ファイル、
ベンダーコード、ロックファイルは規模の判断から除外します。

行数を抑えるために必要なテストやドキュメントを省略しないでください。分割後の各PRが
単独で理解でき、正しさを保てる場合にのみ大きな変更を分割してください。PRが小さくても、
正確性、設計品質、互換性の要件が下がることはありません。

## 作業を始める前に

1. 既存のIssue、PR、コードを検索し、同じ動作やドメインルールがないか確認してください。
2. バグ修正では、可能な限り実際の本番エントリーポイントから再現してください。
3. 所有モジュールを特定し、値や状態がどこで生成、正規化、保存、利用されるか追跡してください。
4. 機能追加では、実装を設計する前に、問題と期待する動作を説明してください。

次の変更は、実装前にIssueまたはDiscussionで相談してください：

- 公開REST、SDK、CLI、MCP、設定のセマンティクス
- 永続データ、ストレージスキーマ、VFS/AGFSパス、暗号化ファイルの動作
- 非同期タスクの所有権、キュー、キャンセル、クリーンアップ、結果状態
- リソースのインポート／監視、セッションライフサイクル、メモリ抽出
- 検索レベル、ディレクトリスコープ、ランキングのセマンティクス
- テナント、アカウント、ユーザー、ピアの識別境界
- 複数の所有モジュールにまたがる変更や大規模なアーキテクチャ変更

現在の動作、提案する動作、具体的なリクエストまたは設定例、互換性への影響を記載して
ください。これにより、実装前にメンテナーが設計境界を確認できます。

リポジトリのGitHubテンプレートを使用して、[バグ報告](https://github.com/volcengine/OpenViking/issues/new?template=bug_report.yml)、
[機能リクエスト](https://github.com/volcengine/OpenViking/issues/new?template=feature_request.yml)、
[質問](https://github.com/volcengine/OpenViking/issues/new?template=question.yml)を提出してください。

## 適切な領域を見つける

影響する領域が分かる場合は、IssueまたはPRに記載してください。不明な場合は、まず観測可能な
動作とユースケースを説明してください。メンテナーが担当領域への振り分けを支援します。

この表は、2026年6月24日から8月24日までにマージされたPRで継続的に確認できる
作成・レビュー活動に基づいています。排他的なコード所有権ではなく、振り分けのための
目安です。変更に直接関係する担当者だけにメンションしてください。

| ドメイン | 領域 | 代表的なパスまたはトピック | 最近活動しているメンテナー／レビュアー |
|---|---|---|---|
| Platform | Server、API、Auth、Identity、Admin、Task | `openviking/server`、`openviking/service` | `@qin-ctx` |
| Resource | 取り込み、Watch、タスクパイプライン | `openviking/resource` | `@qin-ctx`、`@KCHENPENGFEI` |
| Resource | リソース解析 | `openviking/parse` | `@zihengli-bytedance`、`@KCHENPENGFEI` |
| Memory | Session、メモリ抽出、コンパイル | `openviking/session`、メモリ抽出、`ov compile` | `@chenjw`、`@heaoxiang-ai`、`@fujiajie666` |
| Retrieval | SearchとVectorDB | `openviking/retrieve`、`openviking/storage/vectordb` | `@zhoujh01`、`@t0saki` |
| Storage | RAGFS、PathLock、QueueFS、暗号化 | `openviking/storage`、`openviking/pyagfs`、`openviking/crypto`、`crates/ragfs*` | `@baojun-zhang` |
| Integration | Agent PluginとMCP | `agent-plugins`、メモリPluginの例、Server MCP | `@t0saki`、`@ZaynJarvis` |
| Integration | VikingBotとAgentコンパイル | `bot`、`ov compile` | `@yeshion23333`、`@fujiajie666` |
| Client | SDK、CLI、LangChain | `sdk`、`crates/ov_cli`、`integrations/langchain` | `@zhoujh01`、`@t0saki`、`@ehz0ah` |
| Product | Web Studio | `web-studio` | `@yufeng201`、`@ZaynJarvis` |
| Project | ドキュメント、CI、Pluginリリース | `docs`、`.github/workflows` | `@yufeng201`、`@ZaynJarvis` |

複数領域にまたがる変更や担当が不明な場合は、主な影響領域を特定したうえで、
`@qin-ctx`、`@ZaynJarvis`、`@zhoujh01`のいずれかにメンションしてください。

## 開発環境

### 前提条件

- Python 3.10以上
- ソースビルド、Rust Binding、同梱`ov` CLIの開発にはRust 1.91.1以上
- `sdk/go`の開発時のみGo 1.22以上
- C++17対応コンパイラ：GCC 9以上またはClang 11以上
- CMake 3.15以上

Linuxでは`build-essential`をインストールし、必要に応じて`pkg-config`も追加してください。
macOSではXcode Command Line Toolsをインストールしてください。Windowsでのローカル
ネイティブビルドにはCMakeとMinGWをインストールしてください。

### インストール

リポジトリをフォークし、自分のフォークをクローンします：

```bash
git clone https://github.com/YOUR_USERNAME/OpenViking.git
cd OpenViking
```

`uv`の使用を推奨します：

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
uv sync --all-extras
```

環境を確認します：

```bash
uv run python -c "import openviking; print(openviking.__version__)"
```

ローカルサーバーを設定します：

```bash
uv run openviking-server init
uv run openviking-server doctor
```

設定とProviderの例は[設定ガイド](https://docs.openviking.ai/en/guides/01-configuration)を
参照してください。

RAGFS Rust Binding、同梱Rust CLI、C++拡張を変更した場合は、ネイティブコンポーネントを
再ビルドしてください：

```bash
uv pip install -e . --force-reinstall
```

SDK、Integration、Plugin、Benchmarkには追加のセットアップ手順がある場合があります。
各ディレクトリのREADMEまたはパッケージマニフェストを確認してください。

## 変更の実装

### 所有権と設計

- 動作は所有モジュールに実装してください。上位レイヤーは結果を転送または利用し、
  同じルールを再実装しないでください。
- 外部の互換表現は境界で1つの正規ドメインモデルに変換してください。内部ビジネスロジックに
  入力形式の推測を持ち込まないでください。
- クライアント向けの境界では、Server、Network、Timeout、Auth、Conflictの意味ある
  エラー区分を維持してください。
- タスク状態は、その状態を生成したタスクとの因果関係を保つ必要があります。グローバルキューの
  状態や無関係なコールバックから完了を推測しないでください。
- 各値とルールには、信頼できる情報源を1つだけ持たせてください。

局所的なエッジケースがタスク境界、公開セマンティクス、全体アーキテクチャを変え始めた場合は、
メインフローに特殊分岐を追加し続けず、実装を止めて設計議論に戻ってください。

### コードスタイル

PythonではRuffをフォーマットとLintに、mypyを型チェックに使用します。設定行幅は100文字です。

変更したパスに対してチェックを実行してください：

```bash
uv run ruff format <changed-paths>
uv run ruff check <changed-paths>
uv run mypy <changed-paths>
```

公開APIには短く有用なDocstringを付けてください。コードを言い換えるコメントより、明確な名前と
直接的な制御フローを優先してください。

Rust、Go、TypeScript、ドキュメント、Pluginの変更では、各コンポーネントで定義された
フォーマット、Lint、型チェック、テストコマンドを使用してください。

### テスト

変更の影響を受ける、意味のある最小の公開契約と主要な失敗境界を検証してください。

- 既存の価値の高い契約テストを更新することを優先してください。
- デフォルトでは、新しいユニットテストやテストファイルを追加しないでください。
- 長期的な公開契約を保護する場合を除き、プライベートHelperの存在、Mock呼び出し順序、
  単純なフィールド転送、フレームワークの動作をテストしないでください。
- 小さく明確な修正では、自動的に新しいテストを追加する必要はありませんが、検証方法を
  説明する必要があります。
- 一時的な再現、診断、負荷、検証スクリプトは`test_scripts/`に置き、ソース、Benchmark、
  メンテナンススクリプトのディレクトリには置かないでください。

関連するテストを絞って実行します。例：

```bash
uv run pytest tests/client/test_http_client_config.py
uv run pytest tests/server/ -k "search"
```

変更範囲とリスクに応じて必要な場合のみ、Pythonテスト全体を実行してください：

```bash
uv run pytest
```

## Pull Requestの提出

最新の`main`からブランチを作成し、焦点を絞った変更を行って`main`向けにPRを提出します。

コミットメッセージとPRタイトルには
[Conventional Commits](https://www.conventionalcommits.org/)を使用してください：

```text
feat(parser): support xlsx resources
fix(retrieval): preserve rerank score order
docs: clarify server configuration
refactor(storage): remove duplicate path normalization
```

リポジトリのPRテンプレートをすべて記入してください。良いPR説明には次を含めます：

- 変更前後の観測可能な動作
- バグ修正の場合は、根本原因と実際の実行パス
- 影響するエントリーポイントと所有モジュール
- 互換性または移行への影響（該当する場合）
- 実際に実行した検証コマンド
- 問題を再現したのか、コードから推測しただけなのか

Human Involvementの項目は正確に選択してください。AI支援によるコントリビューションも
歓迎しますが、作者は変更に責任を持ち、システムの他の部分との相互作用を説明できる必要が
あります。

提出前に：

- Diff全体を確認し、無関係な変更や意図せず生成された変更を削除してください。
- 置き換えられたHelper、分岐、Mock、コメントが削除されていることを確認してください。
- 公開動作が変わる場合は、関連ドキュメントを更新してください。
- 実行しなかったチェックと具体的な理由を報告し、未実行のテストを実行済みとしないでください。

CIは影響するパスに応じてチェックを実行します。CIの成功は必要条件ですが、作者による検証や
メンテナーレビューの代わりにはなりません。

## ドキュメント

プロジェクトドキュメントは`docs/en/`と`docs/zh/`にあります。コード例は実行可能にし、
明確で簡潔な表現を使用してください。対応する翻訳がある場合は両言語を更新してください。

## コミュニティ

敬意を持ち、包括的かつ建設的に、技術的な議論へ集中してください。自由形式の設計や使用方法の
議論には[GitHub Discussions](https://github.com/volcengine/OpenViking/discussions)を、
対応可能なバグや機能リクエストには
[GitHub Issues](https://github.com/volcengine/OpenViking/issues)を使用してください。
