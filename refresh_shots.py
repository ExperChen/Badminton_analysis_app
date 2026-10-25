"""用一个 GGAB CSV 全量替换 SQLite 的 shots 表。

默认用法：
    python refresh_shots.py

指定文件或只校验：
    python refresh_shots.py path/to/yuxu-shots.csv
    python refresh_shots.py --dry-run

程序会先把 CSV 全部读入并校验。校验通过后备份数据库，再在同一个
SQLite 事务中清空并写入 shots；任何错误都会回滚到导入前的状态。
"""

from __future__ import annotations

import argparse
import csv
import io
import math
import sqlite3
from datetime import datetime
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
LATEST_CSV = BASE_DIR / "exports" / "yuxu-shots-latest.csv"
DEFAULT_CSV = LATEST_CSV if LATEST_CSV.is_file() else BASE_DIR / "yuxu-shots-2026-09-08.csv"
DEFAULT_DB = BASE_DIR / "ggab.db"

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
REQUIRED_TEXT_COLS = {"match_id", "rally_id", "shot_id"}
TRUE_VALUES = {"true", "1", "yes"}
FALSE_VALUES = {"false", "0", "no"}


def convert(column: str, value: str | None):
    """严格转换字段；空字符串作为 SQLite NULL。"""
    if value is None or value.strip() == "":
        return None
    value = value.strip()
    if column in BOOL_COLS:
        lowered = value.lower()
        if lowered not in TRUE_VALUES | FALSE_VALUES:
            raise ValueError(f"布尔值 {value!r} 无法识别")
        return int(lowered in TRUE_VALUES)
    if column in INT_COLS:
        number = float(value)
        if not math.isfinite(number) or not number.is_integer():
            raise ValueError(f"整数值 {value!r} 无效")
        return int(number)
    if column in REAL_COLS:
        number = float(value)
        if not math.isfinite(number):
            raise ValueError(f"数值 {value!r} 无效")
        return number
    return value


def database_columns(connection: sqlite3.Connection) -> list[str]:
    columns = [row[1] for row in connection.execute("PRAGMA table_info(shots)")]
    if not columns:
        raise ValueError("数据库中不存在 shots 表")
    return columns


def read_and_validate(csv_path: Path, columns: list[str]) -> list[tuple]:
    """在改动数据库之前完整读取并验证 CSV。"""
    with csv_path.open("r", newline="", encoding="utf-8-sig") as handle:
        return validate_reader(csv.DictReader(handle, strict=True), columns)


def validate_text(content: str, columns: list[str]) -> list[tuple]:
    return validate_reader(csv.DictReader(io.StringIO(content.lstrip("\ufeff"), newline=""), strict=True), columns)


def validate_reader(reader, columns: list[str]) -> list[tuple]:
    rows: list[tuple] = []
    seen_shot_ids: set[str] = set()
    headers = reader.fieldnames or []
    if len(headers) != len(set(headers)):
        raise ValueError("CSV 表头包含重复列")
    missing = sorted(set(columns) - set(headers))
    extra = sorted(set(headers) - set(columns))
    if missing or extra:
        details = []
        if missing:
            details.append(f"缺少列：{', '.join(missing)}")
        if extra:
            details.append(f"未知列：{', '.join(extra)}")
        raise ValueError("CSV 表头与 shots 表不一致；" + "；".join(details))

    for line_number, source in enumerate(reader, start=2):
        if None in source or any(value is None for value in source.values()):
            raise ValueError(f"CSV 第 {line_number} 行列数不完整")
        try:
            converted = {column: convert(column, source.get(column)) for column in columns}
        except (TypeError, ValueError) as exc:
            raise ValueError(f"CSV 第 {line_number} 行：{exc}") from exc

        blanks = sorted(column for column in REQUIRED_TEXT_COLS if not converted[column])
        if blanks:
            raise ValueError(f"CSV 第 {line_number} 行必填字段为空：{', '.join(blanks)}")
        shot_id = converted["shot_id"]
        if shot_id in seen_shot_ids:
            raise ValueError(f"CSV 第 {line_number} 行 shot_id 重复：{shot_id}")
        seen_shot_ids.add(shot_id)
        rows.append(tuple(converted[column] for column in columns))

    if not rows:
        raise ValueError("CSV 没有数据行，拒绝清空数据库")
    return rows


def backup_database(connection: sqlite3.Connection, database_path: Path) -> Path:
    backup_dir = database_path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    backup_path = backup_dir / f"{database_path.stem}-before-import-{stamp}.db"
    with sqlite3.connect(backup_path) as backup:
        connection.backup(backup)
    return backup_path


def replace_shots(connection: sqlite3.Connection, columns: list[str], rows: list[tuple]) -> None:
    try:
        connection.execute("BEGIN IMMEDIATE")
        write_shots(connection, columns, rows)
        connection.commit()
    except Exception:
        connection.rollback()
        raise


def write_shots(connection: sqlite3.Connection, columns: list[str], rows: list[tuple]) -> None:
    """Write within a transaction owned by the caller."""
    names = ",".join(f'"{column}"' for column in columns)
    placeholders = ",".join("?" for _ in columns)
    insert_sql = f"INSERT INTO shots ({names}) VALUES ({placeholders})"
    connection.execute("DELETE FROM shots")
    connection.executemany(insert_sql, rows)
    count = connection.execute("SELECT COUNT(*) FROM shots").fetchone()[0]
    if count != len(rows):
        raise RuntimeError(f"写入行数异常：预计 {len(rows)}，实际 {count}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="校验 CSV，并全量替换 SQLite shots 表")
    parser.add_argument("csv", nargs="?", type=Path, default=DEFAULT_CSV, help="要导入的单个 CSV 文件")
    parser.add_argument("--database", type=Path, default=DEFAULT_DB, help="SQLite 数据库路径")
    parser.add_argument("--dry-run", action="store_true", help="只校验，不备份或修改数据库")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    csv_path = args.csv.resolve()
    database_path = args.database.resolve()
    if not csv_path.is_file():
        raise SystemExit(f"找不到 CSV：{csv_path}")
    if not database_path.is_file():
        raise SystemExit(f"找不到数据库：{database_path}")

    try:
        with sqlite3.connect(database_path, timeout=30) as connection:
            columns = database_columns(connection)
            rows = read_and_validate(csv_path, columns)
            match_index = columns.index("match_id")
            matches = len({row[match_index] for row in rows})
            print(f"校验通过：{csv_path.name}，{len(rows)} 拍，{matches} 场比赛")
            if args.dry_run:
                print("只校验模式：数据库没有改动")
                return

            old_count = connection.execute("SELECT COUNT(*) FROM shots").fetchone()[0]
            backup_path = backup_database(connection, database_path)
            replace_shots(connection, columns, rows)
            print(f"已备份：{backup_path}")
            print(f"替换完成：删除原有 {old_count} 拍，导入 {len(rows)} 拍 -> {database_path}")
    except (csv.Error, OSError, sqlite3.Error, ValueError, RuntimeError) as exc:
        raise SystemExit(f"导入失败，数据库未被部分更新：{exc}") from exc


if __name__ == "__main__":
    main()
