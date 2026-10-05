"""由 crontab 调用，只接收任务编号。"""

from __future__ import annotations

import sys

from app.crontab.service import run_task_by_id


def main() -> None:
    if len(sys.argv) != 2:
        return
    run_task_by_id(sys.argv[1])


if __name__ == "__main__":
    main()
