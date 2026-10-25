"""兼容入口：使用安全的 shots 全量刷新程序。

用法:
    python import_csv.py                      # 默认导入 2026-09-08 文件
    python import_csv.py path/to/file.csv     # 用指定的单个文件全量替换
"""

import csv
import glob
import sqlite3
import sys
from pathlib import Path

BASE_DIR = Path(__file__).parent
DB_PATH = BASE_DIR / "ggab.db"
SCHEMA_PATH = BASE_DIR / "schema.sql"

# 各列的目标类型
INT_COLS = {
    "game_number", "rally_number", "rally_number_in_game", "rally_shots",
    "score_a_before", "score_b_before", "score_a_after", "score_b_after",
    "shot_number_in_rally",
}
REAL_COLS = {
    "rally_start_s", "rally_end_s", "rally_duration_s", "video_timestamp_seconds",
}
BOOL_COLS = {
    "shared_match", "striker_is_you", "rally_won_by_you",
    "is_deceptive", "is_final_shot",
}


def convert(column, value):
    """把 CSV 里的字符串转成合适的 Python 类型；空值转成 None。"""
    if value is None or value == "":
        return None
    if column in BOOL_COLS:
        return 1 if value.strip().lower() in ("true", "1", "yes") else 0
    if column in INT_COLS:
        return int(float(value))
    if column in REAL_COLS:
        return float(value)
    return value


def main():
    files = sys.argv[1:] or sorted(glob.glob(str(BASE_DIR / "yuxu-shots-*.csv")))
    if not files:
        sys.exit("没有找到 CSV 文件")

    conn = sqlite3.connect(DB_PATH)
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))

    columns = [row[1] for row in conn.execute("PRAGMA table_info(shots)")]
    placeholders = ",".join("?" * len(columns))
    # INSERT OR REPLACE: 重复的 shot_id 会被覆盖，重跑脚本不会产生重复数据
    insert_sql = (
        f"INSERT OR REPLACE INTO shots ({','.join(columns)}) VALUES ({placeholders})"
    )

    total = 0
    for path in files:
        with open(path, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            missing = set(columns) - set(reader.fieldnames or [])
            if missing:
                sys.exit(f"{path} 缺少列: {sorted(missing)}")

            rows = [
                tuple(convert(col, row.get(col)) for col in columns)
                for row in reader
            ]

        conn.executemany(insert_sql, rows)
        conn.commit()
        total += len(rows)
        print(f"已导入 {len(rows):>5} 行  <-  {Path(path).name}")

    count = conn.execute("SELECT COUNT(*) FROM shots").fetchone()[0]
    conn.close()
    print(f"\n完成：读取 {total} 行，数据库现有 {count} 拍 -> {DB_PATH}")


if __name__ == "__main__":
    # 保留旧文件名供已有使用习惯调用，实际统一走带校验、备份和回滚的新流程。
    from refresh_shots import main as safe_refresh_main

    safe_refresh_main()
