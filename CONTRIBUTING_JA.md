# コントリビューションガイド

OpenVikingに興味をお持ちいただきありがとうございます！あらゆる種類のコントリビューションを歓迎します：

- バグレポート
- 機能リクエスト
- ドキュメントの改善
- コードのコントリビューション

---

## 開発環境のセットアップ

### 前提条件

- **Python**: 3.10以上
- **Go**: 1.22以上（AGFSコンポーネントのソースビルドに必要）
- **Rust**: 1.91.1以上（ソースビルド時に同梱の `ov` CLI もビルドされるため必須）
- **C++コンパイラ**: GCC 9以上 または Clang 11以上（コア拡張のビルドに必要、C++17サポートが必須）
- **CMake**: 3.12以上

#### プラットフォーム別のネイティブビルドツール

- **Linux**: `build-essential` の導入を推奨。環境によっては `pkg-config` も必要です
- **macOS**: Xcode Command Line Tools をインストール（`xcode-select --install`）
- **Windows**: ローカルのネイティブビルドには CMake と MinGW を推奨

#### サポートされているプラットフォーム（プリコンパイル済みWheel）

OpenVikingは以下の環境向けにプリコンパイル済み**Wheel**パッケージを提供しています：

- **Windows**: x86_64
- **macOS**: x86_64、arm64（Apple Silicon）
- **Linux**: x86_64、arm64（manylinux）

その他のプラットフォーム（例：FreeBSD）では、`pip`によるインストール時にソースから自動コンパイルされます。[前提条件](#前提条件)がインストールされていることを確認してください。

### 1. フォークとクローン

```bash
git clone https://github.com/YOUR_USERNAME/openviking.git
cd openviking
```

### 2. 依存関係のインストール

Python環境管理には`uv`の使用を推奨します：

```bash
# uvのインストール（未インストールの場合）
curl -LsSf https://astral.sh/uv/install.sh | sh

# 依存関係の同期と仮想環境の作成
uv sync --all-extras
source .venv/bin/activate  # Linux/macOS
# または .venv\Scripts\activate  # Windows
```

#### ローカル開発とネイティブコンポーネントの再ビルド

OpenVikingはAGFSに対してデフォルトで`binding-client`モードを使用し、事前にビルドされたネイティブ成果物を利用します。**AGFS（Go）**コード、同梱の**Rust CLI**、または**C++拡張**を変更した場合や、プリビルド成果物が見つからない場合は、再コンパイルと再インストールが必要です。プロジェクトルートで以下のコマンドを実行してください：

```bash
uv pip install -e . --force-reinstall
```

このコマンドにより`setup.py`が再実行され、AGFS、同梱 `ov` CLI、C++コンポーネントの再ビルドがトリガーされます。

### 3. 環境設定

設定ファイル `~/.openviking/ov.conf` を作成します：

```json
{
  "embedding": {
    "dense": {
      "provider": "volcengine",
      "api_key": "your-api-key",
      "model": "doubao-embedding-vision-251215",
      "api_base": "https://ark.cn-beijing.volces.com/api/v3",
      "dimension": 1024,
      "input": "multimodal"
    }
  },
  "vlm": {
    "api_key": "your-api-key",
    "model": "doubao-seed-2-0-pro-260215",
    "api_base": "https://ark.cn-beijing.volces.com/api/v3"
  }
}
```

環境変数を設定します：

```bash
export OPENVIKING_CONFIG_FILE=~/.openviking/ov.conf
```

### 4. インストールの確認

```python
import asyncio
import openviking as ov

async def main():
    client = ov.AsyncOpenViking(path="./test_data")
    await client.initialize()
    print("OpenViking initialized successfully!")
    await client.close()

asyncio.run(main())
```

### 5. Rust CLIのビルド（オプション）

Rust CLI（`ov`）は、OpenViking Serverとやり取りするための高性能コマンドラインクライアントを提供します。

`ov` を直接使わない場合でも、OpenViking をソースからビルドするなら Rust ツールチェーンは必要です。パッケージング時に同梱 CLI バイナリも一緒にビルドされるためです。

**前提条件**: Rust >= 1.91.1

```bash
# ソースからビルドしてインストール
cargo install --path crates/ov_cli

# または公開済みの npm CLI パッケージをインストール（プリビルドバイナリをダウンロード）
npm i -g @openviking/cli
```

インストール後、`ov --help`を実行して利用可能なすべてのコマンドを確認できます。CLI接続設定は`~/.openviking/ovcli.conf`に記述します。

---

## プロジェクト構成

```
openviking/
├── pyproject.toml        # プロジェクト設定
├── Cargo.toml            # Rustワークスペース設定
├── third_party/          # サードパーティ依存関係
│   └── agfs/             # AGFSファイルシステム
│
├── openviking/           # Python SDK
│   ├── async_client.py   # AsyncOpenVikingクライアント
│   ├── sync_client.py    # SyncOpenVikingクライアント
│   ├── client/           # ローカル / HTTP クライアント実装
│   ├── console/          # スタンドアロン console UI とプロキシサービス
│   ├── core/             # コアデータモデルとディレクトリ抽象
│   ├── message/          # セッションメッセージと part モデル
│   ├── models/           # Embedding / VLM バックエンド
│   ├── parse/            # リソースパーサーと検出器
│   ├── resource/         # リソース処理と watch 管理
│   ├── retrieve/         # 検索システム
│   ├── server/           # HTTPサーバー
│   ├── service/          # 共通 service レイヤー
│   ├── session/          # セッション管理と圧縮
│   ├── storage/          # ストレージレイヤー
│   ├── telemetry/        # オペレーション telemetry
│   ├── trace/            # trace とランタイム追跡補助
│   ├── utils/            # ユーティリティと設定補助
│   └── prompts/          # プロンプトテンプレート
│
├── crates/               # Rustコンポーネント
│   └── ov_cli/           # Rust CLIクライアント
│       ├── src/          # CLIソースコード
│       └── install.sh    # 非推奨スタブ（npm パッケージを使用、Install を参照）
│
├── src/                  # C++拡張ソース（Python abi3）
│
├── tests/                # テストスイート
│   ├── client/           # クライアントテスト
│   ├── console/          # Console テスト
│   ├── core/             # コアロジックテスト
│   ├── parse/            # パーサーテスト
│   ├── resource/         # リソース処理テスト
│   ├── retrieve/         # 検索テスト
│   ├── server/           # サーバーテスト
│   ├── service/          # Service レイヤーテスト
│   ├── session/          # セッションテスト
│   ├── storage/          # ストレージテスト
│   ├── telemetry/        # Telemetry テスト
│   ├── vectordb/         # ベクトルデータベーステスト
│   └── integration/      # E2E テスト
│
└── docs/                 # ドキュメント
    ├── en/               # 英語ドキュメント
    └── zh/               # 中国語ドキュメント
```

---

## コードスタイル

コードの一貫性を維持するために以下のツールを使用しています：

| ツール | 目的 | 設定 |
|------|---------|--------|
| **Ruff** | リンティング、フォーマット、インポートソート | `pyproject.toml` |
| **mypy** | 型チェック | `pyproject.toml` |

### 自動チェック（推奨）

[pre-commit](https://pre-commit.com/)を使用して、コミット前にこれらのチェックを自動実行します。これにより、手動の作業なしでコードが常に基準を満たすことが保証されます。

1. **pre-commitのインストール**:
   ```bash
   pip install pre-commit
   ```

2. **gitフックのインストール**:
   ```bash
   pre-commit install
   ```

これで、`git commit`実行時に`ruff`（チェックとフォーマット）が自動的に実行されます。チェックが失敗した場合、ファイルが自動修正されることがあります。変更をaddして再度コミットするだけです。

### チェックの実行

```bash
# コードのフォーマット
ruff format openviking/

# リント
ruff check openviking/

# 型チェック
mypy openviking/
```

### スタイルガイドライン

1. **行幅**: 100文字
2. **インデント**: スペース4つ
3. **文字列**: ダブルクォートを推奨
4. **型ヒント**: 推奨（必須ではない）
5. **Docstring**: パブリックAPIには必須（最大1〜2行）

---

## テスト

### テストの実行

```bash
# 全テストの実行
pytest

# 特定のテストモジュールの実行
pytest tests/client/ -v
pytest tests/server/ -v
pytest tests/parse/ -v

# 特定のテストファイルの実行
pytest tests/client/test_lifecycle.py

# 特定のテストの実行
pytest tests/client/test_lifecycle.py::TestClientInitialization::test_initialize_success

# キーワードで実行
pytest -k "search" -v

# カバレッジ付きで実行
pytest --cov=openviking --cov-report=term-missing
```

### テストの書き方

テストは`tests/`配下のサブディレクトリに整理されています。プロジェクトは`asyncio_mode = "auto"`を使用しているため、非同期テストに`@pytest.mark.asyncio`デコレーターは**不要**です：

```python
# tests/client/test_example.py
from openviking import AsyncOpenViking


class TestAsyncOpenViking:
    async def test_initialize(self, uninitialized_client: AsyncOpenViking):
        await uninitialized_client.initialize()
        assert uninitialized_client._service is not None
        await uninitialized_client.close()

    async def test_add_resource(self, client: AsyncOpenViking, sample_markdown_file):
        result = await client.add_resource(
            path=str(sample_markdown_file),
            reason="test document"
        )
        assert "root_uri" in result
        assert result["root_uri"].startswith("viking://")
```

共通フィクスチャは`tests/conftest.py`に定義されており、`client`（初期化済み`AsyncOpenViking`）、`uninitialized_client`、`temp_dir`、`sample_markdown_file` などが含まれます。

---

## コントリビューションワークフロー

### 1. ブランチの作成

```bash
git checkout main
git pull origin main
git checkout -b feature/your-feature-name
```

ブランチ命名規則：
- `feature/xxx` - 新機能
- `fix/xxx` - バグ修正
- `docs/xxx` - ドキュメント更新
- `refactor/xxx` - コードリファクタリング

### 2. 変更の実施

- コードスタイルガイドラインに従う
- 新機能にはテストを追加する
- 必要に応じてドキュメントを更新する

### 3. 変更のコミット

```bash
git add .
git commit -m "feat: add new parser for xlsx files"
```

### 4. プッシュとPRの作成

```bash
git push origin feature/your-feature-name
```

その後、GitHubでプルリクエストを作成します。

---

## コミット規約

[Conventional Commits](https://www.conventionalcommits.org/)に従います：

```
<type>(<scope>): <subject>

<body>

<footer>
```

### タイプ

| タイプ | 説明 |
|------|-------------|
| `feat` | 新機能 |
| `fix` | バグ修正 |
| `docs` | ドキュメント |
| `style` | コードスタイル（ロジック変更なし） |
| `refactor` | コードリファクタリング |
| `perf` | パフォーマンス改善 |
| `test` | テスト |
| `chore` | ビルド/ツーリング |

### 例

```bash
# 新機能
git commit -m "feat(parser): add support for xlsx files"

# バグ修正
git commit -m "fix(retrieval): fix score calculation in rerank"

# ドキュメント
git commit -m "docs: update quick start guide"

# リファクタリング
git commit -m "refactor(storage): simplify interface methods"
```

---

## プルリクエストガイドライン

### PRタイトル

コミットメッセージと同じフォーマットを使用します。

### PR説明テンプレート

```markdown
## 概要

変更内容とその目的の簡単な説明。

## 変更の種類

- [ ] 新機能（feat）
- [ ] バグ修正（fix）
- [ ] ドキュメント（docs）
- [ ] リファクタリング（refactor）
- [ ] その他

## テスト

これらの変更のテスト方法を記述してください：
- [ ] ユニットテストが通過する
- [ ] 手動テストが完了している

## 関連Issue

- Fixes #123
- Related to #456

## チェックリスト

- [ ] コードがプロジェクトのスタイルガイドラインに従っている
- [ ] 新機能にテストが追加されている
- [ ] ドキュメントが更新されている（必要な場合）
- [ ] すべてのテストが通過する
```

---

## CI/CDワークフロー

継続的インテグレーションとデプロイメントに**GitHub Actions**を使用しています。ワークフローはモジュール化され、段階的に設計されています。

### 1. 自動ワークフロー

| イベント | ワークフロー | 説明 |
|-------|----------|-------------|
| **プルリクエスト** | `pr.yml` | **Lint**（Ruff、Mypy）と**Test Lite**（Linux + Python 3.10での統合テスト）を実行。コントリビューターに迅速なフィードバックを提供。（**01. Pull Request Checks**として表示） |
| **mainへのプッシュ** | `ci.yml` | **Test Full**（全OS：Linux/Win/Mac、全Pyバージョン：3.10-3.14）と**CodeQL**（セキュリティスキャン）を実行。mainブランチの安定性を保証。（**02. Main Branch Checks**として表示） |
| **リリース公開** | `release.yml` | GitHubでリリースを作成すると発動。自動的にソースディストリビューションとwheelをビルドし、Gitタグからバージョンを判定して**PyPI**に公開。（**03. Release**として表示） |
| **週次Cron** | `schedule.yml` | 毎週日曜日に**CodeQL**セキュリティスキャンを実行。（**04. Weekly Security Scan**として表示） |

このほか、PR review の自動化、Docker イメージのビルド、Rust CLI のパッケージング用ワークフローも用意されています。

### 2. 手動トリガーワークフロー

メンテナーは「Actions」タブから以下のワークフローを手動でトリガーして、特定のタスクを実行したり問題をデバッグしたりできます。

#### A. Lintチェック (`11. _Lint Checks`)
コードスタイルチェック（Ruff）と型チェック（Mypy）を実行。引数は不要です。

> **ヒント**: コミット前にこれらのチェックを自動的に実行するため、ローカルに[pre-commit](https://pre-commit.com/)をインストールすることを推奨します（上記の[自動チェック](#自動チェック推奨)セクションを参照）。

#### B. テストスイート（Lite）(`12. _Test Suite (Lite)`)
高速統合テストを実行し、カスタムマトリックス設定をサポートします。

*   **入力**:
    *   `os_json`: 実行するOSのJSON文字列配列（例：`["ubuntu-24.04"]`）。
    *   `python_json`: Pythonバージョンの JSON文字列配列（例：`["3.10"]`）。

#### C. テストスイート（Full）(`13. _Test Suite (Full)`)
サポートされているすべてのプラットフォーム（Linux/Mac/Win）とPythonバージョン（3.10-3.14）で完全なテストスイートを実行。手動トリガー時にカスタムマトリックス設定をサポートします。

*   **入力**:
    *   `os_json`: 実行するOSのリスト（デフォルト：`["ubuntu-24.04", "macos-14", "windows-latest"]`）。
    *   `python_json`: Pythonバージョンのリスト（デフォルト：`["3.10", "3.11", "3.12", "3.13", "3.14"]`）。

#### D. セキュリティスキャン (`14. _CodeQL Scan`)
CodeQLセキュリティ分析を実行。引数は不要です。

#### E. ディストリビューションビルド (`15. _Build Distribution`)
Pythonのwheelパッケージのみをビルドし、公開はしません。

*   **入力**:
    *   `os_json`: ビルドするOSのリスト（デフォルト：`["ubuntu-24.04", "ubuntu-24.04-arm", "macos-14", "macos-15-intel", "windows-latest"]`）。
    *   `python_json`: Pythonバージョンのリスト（デフォルト：`["3.10", "3.11", "3.12", "3.13", "3.14"]`）。
    *   `build_sdist`: ソースディストリビューションをビルドするか（デフォルト：`true`）。
    *   `build_wheels`: wheelディストリビューションをビルドするか（デフォルト：`true`）。

#### F. ディストリビューション公開 (`16. _Publish Distribution`)
ビルド済みパッケージをPyPIに公開（ビルドRun IDが必要）。

*   **入力**:
    *   `target`: 公開先を選択（`testpypi`、`pypi`、`both`）。
    *   `build_run_id`: ビルドワークフローのRun ID（必須、ビルド実行URLから取得）。

#### G. 手動リリース (`03. Release`)
ワンストップのビルドと公開（ビルドと公開ステップを含む）。

> **バージョン番号とタグ規約**:
> このプロジェクトは`setuptools_scm`を使用してGitタグからバージョン番号を自動抽出します。
> *   **タグ命名規約**: `vX.Y.Z`形式に従う必要があります（例：`v0.1.0`、`v1.2.3`）。タグはセマンティックバージョニングに準拠する必要があります。
> *   **リリースビルド**: リリースイベントがトリガーされると、バージョン番号はGitタグに直接対応します（例：`v0.1.0` -> `0.1.0`）。
> *   **手動/非タグビルド**: バージョン番号には最後のタグからのコミット数が含まれます（例：`0.1.1.dev3`）。
> *   **バージョン確認**: 公開ジョブ完了後、ワークフロー**Summary**ページ上部の**Notifications**エリアで公開バージョンを直接確認できます（例：`Successfully published to PyPI with version: 0.1.8`）。ログまたは**Artifacts**のファイル名でも確認できます。

*   **入力**:
    *   `target`: 公開先を選択。
        *   `none`: アーティファクトのビルドのみ（公開なし）。ビルド機能の検証に使用。
        *   `testpypi`: TestPyPIに公開。ベータテストに使用。
        *   `pypi`: 公式PyPIに公開。
        *   `both`: 両方に公開。
    *   `os_json`: ビルドプラットフォーム（デフォルトはすべて含む）。
    *   `python_json`: Pythonバージョン（デフォルトはすべて含む）。
    *   `build_sdist`: ソースディストリビューションをビルドするか（デフォルト：`true`）。
    *   `build_wheels`: wheelディストリビューションをビルドするか（デフォルト：`true`）。

> **公開に関する注意事項**:
> *   **先にテスト**: 公式PyPIに公開する前に、**TestPyPI**で検証することを強く推奨します。PyPIとTestPyPIは完全に独立した環境であり、アカウントやパッケージデータは共有されません。
> *   **上書き不可**: PyPIもTestPyPIも、同じ名前とバージョンの既存パッケージの上書きを許可しません。再公開が必要な場合は、バージョン番号をアップグレードする必要があります（例：新しいバージョンをタグ付けするか、新しいdevバージョンを生成）。既存のバージョンを公開しようとすると、ワークフローが失敗します。

---

## Issueガイドライン

### バグレポート

以下を提供してください：

1. **環境**
   - Pythonバージョン
   - OpenVikingバージョン
   - オペレーティングシステム

2. **再現手順**
   - 詳細な手順
   - コードスニペット

3. **期待される動作と実際の動作**

4. **エラーログ**（ある場合）

### 機能リクエスト

以下を記述してください：

1. **問題**: どのような問題を解決しようとしていますか？
2. **解決策**: どのような解決策を提案しますか？
3. **代替案**: 他のアプローチを検討しましたか？

---

## ドキュメント

ドキュメントは`docs/`配下にMarkdown形式で管理されています：

- `docs/en/` - 英語ドキュメント
- `docs/zh/` - 中国語ドキュメント

### ドキュメントガイドライン

1. コード例は実行可能であること
2. ドキュメントとコードの同期を維持すること
3. 明確で簡潔な言葉を使用すること

---

## 行動規範

このプロジェクトに参加することで、以下に同意するものとします：

1. **敬意を持つ**: 友好的でプロフェッショナルな態度を維持する
2. **包括的である**: あらゆるバックグラウンドのコントリビューターを歓迎する
3. **建設的である**: 有益なフィードバックを提供する
4. **集中する**: 議論を技術的な内容に保つ

---

## ヘルプ

質問がある場合：

- [GitHub Issues](https://github.com/volcengine/openviking/issues)
- [Discussions](https://github.com/volcengine/openviking/discussions)

---

コントリビューションありがとうございます！
