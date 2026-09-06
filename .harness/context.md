# ハーネス配布元の作業地図

- 製品: Codex Delivery Harness 1.0.0。ソースは .agents/skills/delivery-harness、導入は install.py。
- 実行: Python 3.10+ 標準ライブラリ。確認環境は docs/VALIDATION.md。
- 検証: python3 -m unittest discover -s tests -v。サンプルは examples/run_demo.py。
- 不変条件: 既存設定を保持し、未検証を成功にせず、ローカルREADYを本番許可と混同しない。
- 外部接続: この配布物に組織固有のCI・本番基盤・運用担当は未接続。
- 保守担当: 採用するプロジェクトで割り当てる。
- 確認日: 2026-09-05。根拠: 実ソース、テスト、docs/SOURCES.md。
