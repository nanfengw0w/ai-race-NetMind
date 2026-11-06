import sqlite3
import threading
from config import DB_PATH

_connections = threading.local()

def get_db():
    connection = getattr(_connections, "connection", None)
    if connection is None:
        connection = sqlite3.connect(DB_PATH, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=30000")
        _connections.connection = connection
    return connection

def init_db():
    db = get_db()
    db.executescript("""
    CREATE TABLE IF NOT EXISTS students (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        owner_id TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS user_accounts (
        id TEXT PRIMARY KEY,
        display_name TEXT DEFAULT 'NetMind 学习者',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS user_memories (
        user_id TEXT PRIMARY KEY,
        enabled INTEGER DEFAULT 1,
        content TEXT DEFAULT '',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES user_accounts(id)
    );

    CREATE TABLE IF NOT EXISTS profiles (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        student_id TEXT NOT NULL,
        profile_json TEXT NOT NULL,
        version INTEGER DEFAULT 1,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (student_id) REFERENCES students(id)
    );

    CREATE TABLE IF NOT EXISTS conversations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        student_id TEXT NOT NULL,
        role TEXT NOT NULL,
        content TEXT NOT NULL,
        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (student_id) REFERENCES students(id)
    );

    CREATE TABLE IF NOT EXISTS resources (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        student_id TEXT NOT NULL,
        type TEXT NOT NULL,
        title TEXT,
        content TEXT NOT NULL,
        metadata_json TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (student_id) REFERENCES students(id)
    );

    CREATE TABLE IF NOT EXISTS quizzes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        resource_id INTEGER,
        student_id TEXT NOT NULL,
        question_json TEXT NOT NULL,
        answer_json TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (resource_id) REFERENCES resources(id),
        FOREIGN KEY (student_id) REFERENCES students(id)
    );

    CREATE TABLE IF NOT EXISTS quiz_records (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        student_id TEXT NOT NULL,
        quiz_id INTEGER NOT NULL,
        user_answer TEXT,
        is_correct INTEGER,
        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (student_id) REFERENCES students(id),
        FOREIGN KEY (quiz_id) REFERENCES quizzes(id)
    );

    CREATE TABLE IF NOT EXISTS learning_paths (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        student_id TEXT NOT NULL,
        path_json TEXT NOT NULL,
        status TEXT DEFAULT 'active',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (student_id) REFERENCES students(id)
    );

    CREATE TABLE IF NOT EXISTS mastery_states (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        student_id TEXT NOT NULL,
        topic TEXT NOT NULL,
        score REAL DEFAULT 0.5,
        attempts INTEGER DEFAULT 0,
        correct_count INTEGER DEFAULT 0,
        wrong_count INTEGER DEFAULT 0,
        last_action TEXT,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(student_id, topic),
        FOREIGN KEY (student_id) REFERENCES students(id)
    );

    CREATE TABLE IF NOT EXISTS tutor_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        student_id TEXT NOT NULL,
        request TEXT NOT NULL,
        plan_json TEXT NOT NULL,
        reflection_json TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (student_id) REFERENCES students(id)
    );

    CREATE TABLE IF NOT EXISTS tutor_followups (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        student_id TEXT NOT NULL,
        quiz_id INTEGER NOT NULL,
        action TEXT NOT NULL,
        resource_ids_json TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(student_id, quiz_id, action),
        FOREIGN KEY (student_id) REFERENCES students(id)
    );

    CREATE TABLE IF NOT EXISTS token_usage (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT,
        student_id TEXT,
        model TEXT,
        prompt_tokens INTEGER DEFAULT 0,
        completion_tokens INTEGER DEFAULT 0,
        total_tokens INTEGER DEFAULT 0,
        estimated INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS learning_activity (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT NOT NULL,
        student_id TEXT,
        active_seconds INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS learning_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT NOT NULL,
        student_id TEXT,
        event_type TEXT NOT NULL,
        resource_id INTEGER,
        resource_type TEXT,
        title TEXT,
        topic TEXT,
        value INTEGER DEFAULT 0,
        metadata_json TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (student_id) REFERENCES students(id),
        FOREIGN KEY (resource_id) REFERENCES resources(id)
    );

    CREATE TABLE IF NOT EXISTS notes (
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL,
        student_id TEXT,
        type TEXT NOT NULL,
        content TEXT NOT NULL,
        source TEXT,
        options_json TEXT,
        answer TEXT,
        explanation TEXT,
        html TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS understanding_checks (
        id TEXT PRIMARY KEY,
        student_id TEXT NOT NULL,
        conversation_id INTEGER,
        resource_id INTEGER NOT NULL,
        version INTEGER NOT NULL,
        content_hash TEXT NOT NULL,
        private_json TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (student_id) REFERENCES students(id),
        FOREIGN KEY (conversation_id) REFERENCES conversations(id)
    );
    CREATE INDEX IF NOT EXISTS idx_understanding_conversation
        ON understanding_checks(conversation_id);

    """)
    # Add the new schema to development databases. Pre-versioning records are
    # not promoted or backfilled; artifact APIs only expose actual snapshots.
    resource_columns = {
        row["name"] for row in db.execute("PRAGMA table_info(resources)").fetchall()
    }
    if "current_version" not in resource_columns:
        db.execute("ALTER TABLE resources ADD COLUMN current_version INTEGER NOT NULL DEFAULT 0")
    db.executescript("""
    CREATE TABLE IF NOT EXISTS resource_versions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        resource_id INTEGER NOT NULL,
        version INTEGER NOT NULL CHECK (version > 0),
        type TEXT NOT NULL,
        title TEXT,
        content TEXT NOT NULL,
        metadata_json TEXT,
        parent_version INTEGER,
        change_request TEXT,
        source_conversation_id INTEGER,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        content_hash TEXT NOT NULL,
        UNIQUE(resource_id, version),
        FOREIGN KEY (resource_id) REFERENCES resources(id) ON DELETE CASCADE
    );
    CREATE TABLE IF NOT EXISTS resource_source_messages (
        source_message_id INTEGER PRIMARY KEY,
        resource_id INTEGER NOT NULL UNIQUE,
        FOREIGN KEY (resource_id) REFERENCES resources(id) ON DELETE CASCADE
    );
    CREATE INDEX IF NOT EXISTS idx_resource_versions_resource
        ON resource_versions(resource_id, version DESC);
    CREATE TABLE IF NOT EXISTS conversation_checkpoints (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        student_id TEXT NOT NULL,
        through_id INTEGER NOT NULL,
        summary TEXT NOT NULL,
        model TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(student_id, through_id),
        FOREIGN KEY (student_id) REFERENCES students(id) ON DELETE CASCADE
    );
    CREATE TABLE IF NOT EXISTS conversation_resource_refs (
        conversation_id INTEGER PRIMARY KEY,
        resource_id INTEGER NOT NULL,
        version INTEGER NOT NULL,
        FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE,
        FOREIGN KEY (resource_id) REFERENCES resources(id) ON DELETE CASCADE
    );
    CREATE TRIGGER IF NOT EXISTS resource_versions_immutable
    BEFORE UPDATE ON resource_versions BEGIN
        SELECT RAISE(ABORT, 'resource versions are immutable');
    END;
    """)
    conversation_columns = {
        row["name"] for row in db.execute("PRAGMA table_info(conversations)").fetchall()
    }
    if "artifact_action_json" not in conversation_columns:
        db.execute("ALTER TABLE conversations ADD COLUMN artifact_action_json TEXT")
    reference_columns = {
        row["name"] for row in db.execute("PRAGMA table_info(conversation_resource_refs)").fetchall()
    }
    if "selection_json" not in reference_columns:
        db.execute("ALTER TABLE conversation_resource_refs ADD COLUMN selection_json TEXT")
    student_columns = {
        row["name"] for row in db.execute("PRAGMA table_info(students)").fetchall()
    }
    if "owner_id" not in student_columns:
        db.execute("ALTER TABLE students ADD COLUMN owner_id TEXT")
    db.execute("CREATE INDEX IF NOT EXISTS idx_students_owner ON students(owner_id)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_token_usage_user ON token_usage(user_id)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_learning_activity_user ON learning_activity(user_id)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_learning_events_student ON learning_events(student_id)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_learning_events_user ON learning_events(user_id)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_learning_events_resource ON learning_events(resource_id)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_notes_user ON notes(user_id)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_notes_student ON notes(student_id)")

    # BKT columns for mastery_states
    mastery_columns = {
        row["name"] for row in db.execute("PRAGMA table_info(mastery_states)").fetchall()
    }
    bkt_additions = [
        ("p_mastery", "REAL DEFAULT 0.3"),
        ("p_init", "REAL DEFAULT 0.3"),
        ("p_transit", "REAL DEFAULT 0.1"),
        ("p_guess", "REAL DEFAULT 0.25"),
        ("p_slip", "REAL DEFAULT 0.1"),
        ("decay_rate", "REAL DEFAULT 0.05"),
        ("last_reviewed_at", "TIMESTAMP"),
        ("review_count", "INTEGER DEFAULT 0"),
    ]
    for col_name, col_type in bkt_additions:
        if col_name not in mastery_columns:
            db.execute(f"ALTER TABLE mastery_states ADD COLUMN {col_name} {col_type}")

    db.commit()
