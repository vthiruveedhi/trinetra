-- trinetra Phase 1 event schema.
-- One run of analyze.py = one row in `sessions`. All other tables FK back.

CREATE TABLE IF NOT EXISTS sessions (
    session_id        TEXT PRIMARY KEY,
    source_video      TEXT NOT NULL,
    started_at        TEXT NOT NULL,
    duration_seconds  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS person_events (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id             TEXT NOT NULL,
    tracker_id             INTEGER NOT NULL,
    first_seen_at          REAL NOT NULL,
    last_seen_at           REAL NOT NULL,
    total_visible_seconds  REAL NOT NULL,
    zones_visited          TEXT,
    FOREIGN KEY (session_id) REFERENCES sessions(session_id)
);

CREATE TABLE IF NOT EXISTS zone_dwells (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      TEXT NOT NULL,
    tracker_id      INTEGER NOT NULL,
    zone_name       TEXT NOT NULL,
    entered_at      REAL NOT NULL,
    exited_at       REAL NOT NULL,
    dwell_seconds   REAL NOT NULL,
    FOREIGN KEY (session_id) REFERENCES sessions(session_id)
);

CREATE TABLE IF NOT EXISTS line_crossings (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id   TEXT NOT NULL,
    tracker_id   INTEGER NOT NULL,
    line_name    TEXT NOT NULL,
    direction    TEXT NOT NULL,
    crossed_at   REAL NOT NULL,
    FOREIGN KEY (session_id) REFERENCES sessions(session_id)
);

CREATE INDEX IF NOT EXISTS idx_dwell_tracker  ON zone_dwells(session_id, tracker_id);
CREATE INDEX IF NOT EXISTS idx_dwell_zone     ON zone_dwells(zone_name);
CREATE INDEX IF NOT EXISTS idx_line_tracker   ON line_crossings(session_id, tracker_id);
CREATE INDEX IF NOT EXISTS idx_person_session ON person_events(session_id);
