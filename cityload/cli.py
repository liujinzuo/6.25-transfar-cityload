"""命令行入口。

    python -m cityload.cli data        # 生成合成算例并导出 CSV
    python -m cityload.cli benchmark   # 跑对照实验，输出 reports/benchmark.{md,json}
    python -m cityload.cli viz         # 生成 3D 装卸示意图 reports/loadplan.html
    python -m cityload.cli submission  # 生成单文件提交说明页 SUBMISSION.html
    python -m cityload.cli all         # 以上全部（默认命令）

可选 ``--data DIR`` 用 CSV 目录替换合成数据（字段结构见 docs/04-数据说明.md）。
"""

from __future__ import annotations

import argparse
import os
import sys

from .data_synth import describe
from .dataio import DataFormatError, export_instance
from .pipeline import ConfigError, build_context, load_config, run_benchmark
from .report import write_outputs
from .submission import write_submission

DEFAULT_CONFIG = os.path.join("configs", "default.json")

EXIT_OK = 0
EXIT_CONFIG = 2
EXIT_DATA = 3
EXIT_UNEXPECTED = 4


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="cityload",
        description="城市配送多约束智能配载与运输调度（传化 × 动势 AI 架构师大赛 · AI＋物流赛道）",
    )
    p.add_argument(
        "command",
        nargs="?",
        default="all",
        choices=["data", "benchmark", "viz", "submission", "all"],
        help="要执行的步骤，默认 all",
    )
    p.add_argument("--config", default=DEFAULT_CONFIG, help="配置文件路径")
    p.add_argument("--data", default=None, help="用该目录下的 CSV 作为输入（替代合成数据）")
    p.add_argument("--out", default="reports", help="报告与可视化输出目录")
    p.add_argument("--data-out", default=os.path.join("data", "synthetic"),
                   help="合成算例 CSV 输出目录")
    p.add_argument("--viz-plan", default="D", help="用哪个方案生成 3D 示意图（A/B/C/D）")
    p.add_argument("--submission", default="SUBMISSION.html",
                   help="单文件提交说明页的输出路径")
    p.add_argument("--quiet", action="store_true", help="只输出结果，不打印过程")
    return p


def main(argv: list[str] | None = None) -> int:
    """命令行入口。

    所有可预期的失败（配置缺失/不合法、数据目录缺文件、文件打不开）
    都以人话提示 + 明确退出码返回，绝不把 Python 堆栈甩给使用者。
    退出码：0 成功 / 2 配置问题 / 3 数据问题 / 4 其它 I/O 问题。
    """

    args = _parser().parse_args(argv)
    if not os.path.exists(args.config):
        print(f"[✗] 找不到配置文件：{args.config}\n"
              f"    仓库自带 configs/default.json 与 configs/test.json，"
              f"或用 --config 指定路径。", file=sys.stderr)
        return EXIT_CONFIG
    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print(f"[✗] {exc}", file=sys.stderr)
        return EXIT_CONFIG
    verbose = not args.quiet

    try:
        return _run(args, config, verbose)
    except DataFormatError as exc:
        print(f"[✗] {exc}", file=sys.stderr)
        return EXIT_DATA
    except OSError as exc:
        print(f"[✗] 读写文件失败：{exc}", file=sys.stderr)
        return EXIT_UNEXPECTED


def _run(args, config: dict, verbose: bool) -> int:
    if args.command in ("data", "all"):
        inst, _, _, _ = build_context(config, data_dir=args.data)
        files = export_instance(inst, args.data_out)
        if verbose:
            s = describe(inst)
            print(f"[数据] 已导出合成算例到 {args.data_out}/"
                  f"（{s['stores']} 门店 / {s['order_lines']} 订单行）")
            for name, path in files.items():
                print(f"       {name:11s} → {path}")

    if args.command in ("benchmark", "viz", "submission", "all"):
        # viz / submission 也需要先求解才能拿到装车落位，三个命令共用同一条流水线
        result = run_benchmark(config, data_dir=args.data, verbose=verbose)
        outs = write_outputs(result, args.out, viz_plan_key=args.viz_plan)
        if args.command in ("submission", "all"):
            outs["submission"] = write_submission(
                out_path=args.submission,
                reports_dir=args.out,
                compact_dir=os.path.join("reports", "compact"),
            )
        if verbose:
            print("[输出]")
            for name, path in outs.items():
                print(f"       {name:13s} → {path}")
        return EXIT_OK

    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
