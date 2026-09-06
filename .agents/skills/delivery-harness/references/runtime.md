# 導入・実行・証拠

このファイルはハーネスの導入、記録または機械判定が必要なときだけ読む。
Python 3.10以上。実行時の追加パッケージ、APIキー、特定クラウドは不要。Codex自体は通常の利用環境を使う。

## 既存プロジェクトにつなぐ

配布物の `install.py --target <既存ディレクトリ>` を使う。既存ファイルに衝突したら中断する。
同じ版の再実行ではプロジェクトの変更を保持する。`--dry-run` は変更予定を表示する。
`.harness/project.json` に、リポジトリで実在を確認したコマンドを登録する。
標準テンプレートの `checks: {}` は未接続であり、実装系の gate は通らない。

```json
{
  "schema_version": 1,
  "name": "example-python-service",
  "checks": {
    "unit": {
      "argv": ["{python}", "-m", "unittest", "discover", "-s", "tests"],
      "cwd": ".",
      "timeout_seconds": 120,
      "category": "test",
      "risks": ["low", "medium", "high", "critical"],
      "kinds": ["feature", "bug", "refactor", "test", "migration", "security", "performance", "data", "ml", "platform"]
    }
  },
  "fingerprint_exclude": [],
  "max_snapshot_bytes": 536870912,
  "risk_rules": [{"patterns": ["migrations/**", "**/migrations/**"], "signal": "schema"}]
}
```

例のテストフレームワークを採用する必要はない。既存の package.json、Makefile、CI、lockfileを調べる。
`argv` はシェル文字列ではなく配列。`{python}` は今実行しているPythonに置換される。
Nodeは `npm` / `pnpm` 等、JVMはwrapper、Goは `go test`、Rustは `cargo test` など実際の入口を登録する。
Windowsでは必要なら `npm.cmd` 等の実行可能名を指定する。シェル構文が必要なら、そのシェルを明示する。
その場合シェル文字列はプロジェクト側で管理し、外部入力を連結しない。
実行環境の権限を引き継ぐので、コマンドは信頼できる作業範囲で初回に確認する。
`category` は `test/static/build/other`。省略した `risks` と `kinds` は全対象。
該当チェックは全て必須。`--only` は反復を速める指定で、gate の必須項目を減らさない。

以下ではカレントディレクトリを対象ルートとする。長いパスはCodexがコマンドを組み立てればよい。

```bash
python3 .agents/skills/delivery-harness/scripts/harness.py doctor
python3 .agents/skills/delivery-harness/scripts/harness.py init --id fix-timeout --kind bug --risk medium --risk-reason "外部API失敗時の局所的な挙動変更" --title "タイムアウトの復旧" --goal "依存先が応答しなくてもリクエストが収束する" --acceptance "タイムアウトが指定時間内に返り、再試行が上限を超えない"
python3 .agents/skills/delivery-harness/scripts/harness.py check --id fix-timeout --list
python3 .agents/skills/delivery-harness/scripts/harness.py check --id fix-timeout
python3 .agents/skills/delivery-harness/scripts/harness.py inspect --id fix-timeout --check unit
python3 .agents/skills/delivery-harness/scripts/harness.py evidence --id fix-timeout --criterion A1 --check unit --path tests/test_timeout.py --summary "タイムアウト境界と再試行上限を実行して確認"
```

`python3` がないWindows環境は `py -3` または `python` を使う。例のファイル名とチェックIDは実物に合わせる。
レビューのメモは `.harness/tasks/fix-timeout/review.md` 等へ保存してから参照する。

```bash
python3 .agents/skills/delivery-harness/scripts/harness.py review --id fix-timeout --reviewer codex-author --basis self --verdict pass --path .harness/tasks/fix-timeout/review.md --summary "エラー伝播・待ち時間・キャンセル・無関係な差分を確認"
python3 .agents/skills/delivery-harness/scripts/harness.py gate --id fix-timeout --json
python3 .agents/skills/delivery-harness/scripts/harness.py close --id fix-timeout --summary "上限内に収束し、回帰テストを確認"
```

証拠は短い要約と、実際に確認したファイルを必要とする。文書・調査には出典付き成果物、UIには
観察メモと画像等、実装には意味のあるテストや検証結果を使う。存在するだけの空文書は証拠にならない。
high/critical の独立レビューは `--basis independent --reviewer <別の判断主体>`。
この宣言は認証されない。人、許可された別エージェント、別セッションが実際に評価した場合に限る。
`request-changes` は同じレビュー担当の解消レビューが記録されるまで残る。

## リスクごとの追加証拠

| 条件 | gate が要求する証拠 |
|---|---|
| 全タスク | 受入条件ごとの記録、該当するチェック全部、明示blockerなし |
| 実装系のkind | category=test の該当チェックが最低1つ |
| medium以上 | 現在の入力に対するレビュー |
| high以上 | 独立レビュー、recovery |
| 本番・release・incident・decommission | observability |
| schema・migration | compatibility、data-integrity |
| public-api | compatibility |
| auth・sensitive-data・payments | security |
| payments | data-integrity |
| critical | observability、security（他の該当項目に追加） |

`control --id <id> --name recovery --path <成果物> --summary <実際の確認>` で記録する。
同じ成果物に複数の制御の実証があれば、そのファイルを各項目から参照できる。
計画・調査・レビュー段階は、確認された制約、設計上の復旧案、未知の点を明記した資料が成果となる。
実行段階では計画だけを実証の代用にせず、対象環境に応じたリハーサルや観測を行う。
機械はその内容の正しさや対象環境を判定できない。

## 鮮度と限界

- 入力の内容・実行ビット、タスク契約、リスクから導く要件にハッシュを付ける。チェック中に変われば `input-changed`。
  成功後の変更は `stale`。元と全く同じ入力へ戻した場合は再使用できる。
- 判定範囲はルート内の入力全体。無関係な文書を変えても過去のgateはstaleになる場合がある。
  保守的な検出と反復速度のトレードオフであり、依存関係を推測した部分的な鮮度判定はしない。
  小さな単独修正ではSKILLの短い経路を使い、過去の全タスクの再検証を日常作業へ義務付けない。
- Gitではtrackedとgitignore対象外のuntrackedを対象にする。Gitがなければファイル探索。
  `.harness/tasks/` は自己参照を避けるため除外し、証拠として参照したファイルは別途ハッシュで追跡する。
  未追跡の一般的な依存・生成ディレクトリも除外。trackedの生成物は明示除外しない限り追跡する。
- `fingerprint_exclude` は既知の生成物だけに使う。テスト結果を通すためにソースを隠さない。
  無視したファイル、外部サービス、環境変数、時刻、DB内容はスナップショットで追跡されない。
  それらが変わった検証は再実行し、環境・fixture・依存版を証拠資料に残す。
- シンボリックリンクとサブモジュールディレクトリは機械検証では拒否する。該当コンポーネントの実体を
  対象ルートにするか、独立した既存CIで検証し制約を記録する。大きなモノレポは責任境界ごとに導入する。
- task内の書込みはロック。チェックは順次実行。複数エージェントは別タスクまたはworktreeを使う。
  システム停止で `.lock` が残ったら所有プロセスの終了を確認してから削除する。
- ログは末尾64 KiBのみ保存。代表的な秘密値を伏せるが完全な検出器ではない。
  機密を出すチェックは設定しない。公開する証拠は内容を確認する。
  `check` が表示したrecordファイルまたは `inspect --id <id> --check <name>` で結果と失敗ログを読む。
- タイムアウトはPOSIXではプロセスグループ、Windowsではtaskkillで子プロセスも停止する。
  プロセス分離を自ら脱出するコードの隔離は提供しない。
- 終了コードは `0=成功/READY`、`1=検証不合格/NOT READY`、`2=入力・環境エラー`。
  `doctor=0` は設定を読めたという意味。未設定チェックの警告を完了判定に置き換えない。

状態機械は `init → check/evidence/review/control → gate → close`。
`close` は完了時の記録を残す。ソース変更後は必ず `gate` を再実行し、過去のcompletion.jsonだけで判定しない。
この仕組みはローカルファイルを信頼する。改ざん耐性・職務分離が必要なら保護されたCIと承認サービスにつなぐ。
