# 設計根拠と一次資料

確認日: 2026-09-05。公開資料から設計原則を抽出し、このハーネス向けに実装した。
企業の社内ハーネスの全容を検証したものでも、各社の成果を再現したという実証でもない。
リスク4段階、CLI、証拠の形式、導入段階は本成果物の設計判断。

| 一次資料 | 読み取った実践 | 本成果物への適用 |
|---|---|---|
| [OpenAI: AGENTS.md](https://learn.chatgpt.com/docs/agent-configuration/agents-md) | 階層的な指示探索、override、コンテキスト量の制約 | 短い追記と適用範囲の尊重、overrideへの対応 |
| [OpenAI: Build skills](https://learn.chatgpt.com/docs/build-skills) | SKILLと必要時だけ読む参照、リポジトリのスキル探索 | `.agents/skills`、1つの入口、業務別の参照 |
| [OpenAI: Non-interactive mode](https://learn.chatgpt.com/docs/non-interactive-mode) | 明示したsandbox、JSONLイベント、最終応答のschema | 任意のCLI接続例と結果schema |
| [Google: Small CLs](https://google.github.io/eng-practices/review/developer/small-cls.html) | 理解・レビュー・提供しやすい小さな変更 | 縦の切片、局所的な反復、短い経路 |
| [Google SRE: Error Budget Policy](https://sre.google/workbook/error-budget-policy/) | 利用者の信頼性を根拠に改善の配分を決める | SLOとバジェットの方針、運用学習への接続 |
| [AWS: Staggered deployment strategies](https://docs.aws.amazon.com/wellarchitected/latest/devops-guidance/dl.ads.3-use-staggered-deployment-and-release-strategies.html) | 小さい対象から展開し観測する | 段階提供、拡大・中止条件、範囲の制御 |
| [AWS: CodePipeline rollbacks](https://aws.amazon.com/blogs/devops/de-risk-releases-with-aws-codepipeline-rollbacks/) | 復旧を事前に設計し、復旧の安全性も検証する | 復旧計画と実証のcontrol |
| [Netflix: Automated Canary Analysis with Kayenta](https://netflixtechblog.com/automated-canary-analysis-at-netflix-with-kayenta-3260bc7acc69) | 基線との計測比較と判断、入力データの検証 | 観測基線と十分なデータを要求する提供手順 |
| [Stripe: Online migrations at scale](https://stripe.com/blog/online-migrations) | 段階的な書込み・読み取り切替と照合 | 互換性、バックフィル、整合性、段階ごとの復旧可能性 |
| [DORA: Software delivery performance metrics](https://dora.dev/guides/dora-metrics/) | 速度と不安定性を複数指標で測り、サービス単位で改善する | 指標の契約と基線、再作業と品質も見る改善運用 |

AWS Builders' Libraryの「Automating safe, hands-off deployments」は新しいBuilder Centerへ転送され、
この確認環境では転送先本文を抽出できなかった。その本文からの詳細な引用には依存せず、上記の取得できたAWS一次資料を使用した。
各社の展開率、観測時間、SLOを汎用的な既定値へコピーしていない。

## 設計判断

実行できる共通部分は証拠の保存、入力の鮮度、コマンドの結果、最低限の要件充足に限定した。
技術・ドメインごとの正しさは既存テスト・専門判断・実環境の観測で担保する。
これにより、言語や提供基盤が変わっても共通の進め方を使い、検証の中身はプロジェクトに適合させられる。

認証された独立レビューや本番権限の判定をローカルJSONへ偽装しない。
チームの責任、保護されたCI、監視、本番での学習を組み合わせて初めて組織能力になる。
この境界を隠した「万能」や「企業と同等」の保証は行わない。
