-- GGAB 羽毛球击球数据库 —— 单表结构
-- 每行 = 一拍 (one shot)

DROP TABLE IF EXISTS shots;

CREATE TABLE shots (
    -- 标识
    match_id                TEXT    NOT NULL,
    rally_id                TEXT    NOT NULL,
    shot_id                 TEXT    PRIMARY KEY,

    -- 比赛信息
    match_title             TEXT,
    match_date              TEXT,               -- YYYY-MM-DD
    game_type               TEXT,               -- singles / doubles
    shared_match            INTEGER,            -- 0 / 1
    shared_by               TEXT,

    -- 击球者
    striker                 TEXT,               -- A1 / B1 ...
    striker_name            TEXT,
    striker_is_you          INTEGER,            -- 0 / 1
    striker_handedness      TEXT,               -- left / right

    -- 局与回合
    game_number             INTEGER,
    rally_number            INTEGER,
    rally_number_in_game    INTEGER,
    rally_shots             INTEGER,
    rally_start_s           REAL,
    rally_end_s             REAL,
    rally_duration_s        REAL,

    -- 比分
    score_a_before          INTEGER,
    score_b_before          INTEGER,
    score_a_after           INTEGER,
    score_b_after           INTEGER,
    rally_won_by_you        INTEGER,            -- 0 / 1
    rally_end_type          TEXT,

    -- 单拍细节
    shot_number_in_rally    INTEGER,
    video_timestamp_seconds REAL,
    striking_side           TEXT,               -- Forehand / Backhand
    shot_family             TEXT,
    shot_family_code        TEXT,
    shot_variant            TEXT,
    shot_variant_code       TEXT,
    direction               TEXT,
    serve_placement         TEXT,
    contact_point           TEXT,
    outcome                 TEXT,
    error_outcome           TEXT,
    is_deceptive            INTEGER,            -- 0 / 1
    is_final_shot           INTEGER             -- 0 / 1
);

-- 常用查询索引
CREATE INDEX idx_shots_match       ON shots(match_id);
CREATE INDEX idx_shots_rally       ON shots(rally_id);
CREATE INDEX idx_shots_striker     ON shots(striker_name);
CREATE INDEX idx_shots_family      ON shots(shot_family);
CREATE INDEX idx_shots_date        ON shots(match_date);
