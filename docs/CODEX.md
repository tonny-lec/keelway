# Codexへの接続

2026-09-05確認。ローカルCLIは `codex-cli 0.153.4` の `exec --help` と公式文書を確認した。

## デスクトップ／CLI／IDE

リポジトリの `.agents/skills/delivery-harness/SKILL.md` とAGENTS追記が入口。
導入後は対象ディレクトリから開始する。上位・下位の指示が存在する場合は適用範囲を確認する。
AGENTSはセッション開始時に探索され、同じ階層ではoverrideが優先する。総量の既定上限は32 KiB。
新しいセッションで読み込みを確認する。
[公式AGENTS仕様](https://learn.chatgpt.com/docs/agent-configuration/agents-md)

この配布物はユーザーのグローバル設定、モデル、承認モード、MCP接続を変更しない。
通常のモデル・アカウント設定を維持し、特定の有料サービスやAPIキーをハーネスの実行条件にしない。

## 任意の非対話実行

既に導入・接続された対象リポジトリで、例えば次のように使える。
下記は利用者が依頼したローカル変更を実行する例で、自動では実行しない。

```bash
codex exec --sandbox workspace-write \
  --output-schema .agents/skills/delivery-harness/assets/result.schema.json \
  '$delivery-harness この不具合を調べ、依頼された範囲で修正と検証を完了してください。'
```

`--json` を加えるとイベントをJSONLで受け取れる。`--output-last-message` で最終結果を保存できる。
保存先は作成済みタスク配下など、入力スナップショットを不用意に変えない場所を選ぶ。
最終JSONの `complete` は依頼の達成、`prepared` は成果が準備できて追加操作待ち、`blocked` は進行を妨げる条件あり。
これは応答の形式であり、gateの実行結果や承認証跡を代替しない。
[公式非対話モード](https://learn.chatgpt.com/docs/non-interactive-mode)

CLIのこの例は構文を確認した。別のモデル呼出し・外部CIへの導入・利用者リポジトリでの実行は本配布物の検証には含めていない。

## CIでの使い方

ランナーが持つ既存環境で、設定した検証と `gate --id <id> --json` を実行し、終了コードを判定できる。
保存対象にはタスク契約、実行結果、レビュー、変更SHA、環境識別子、ハーネス版を含める。
タスク台帳がローカルだけに存在する場合は、CIへ引き渡す証拠を明示的に選ぶ。
デフォルトではtaskのログはgitignore対象なので、無条件に `git add -f` で共有しない。

gateを信頼するにはゲート実装・設定・証拠の書込み主体をCI側で保護する必要がある。
署名、本人性、職務分離、保護ブランチ、本番承認は既存の仕組みで担保する。
汎用ハーネスがローカルJSONだけでそれらを保証できるとは扱わない。
