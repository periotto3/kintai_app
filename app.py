from __future__ import annotations

import sqlite3
from datetime import datetime, date
from pathlib import Path
from typing import Optional

from flask import Flask, g, redirect, render_template, request, url_for, flash

APP_DIR = Path(__file__).parent
DB_PATH = APP_DIR / "kintai.db"

app = Flask(__name__)
app.secret_key = "dev-secret"  # 自分用なので簡易。公開運用なら環境変数へ。

# -------------------------
# DB helpers
# -------------------------
def get_db() -> sqlite3.Connection:
    if "db" not in g:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        g.db = conn
    return g.db

@app.teardown_appcontext
def close_db(_exc):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()

def init_db() -> None:
    db = get_db()
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS work_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            work_date TEXT NOT NULL UNIQUE,     -- YYYY-MM-DD
            clock_in TEXT,                      -- ISO datetime string
            clock_out TEXT                      -- ISO datetime string
        );
        """
    )
    db.commit()

@app.before_request
def _ensure_db():
    init_db()

# -------------------------
# business logic
# -------------------------
def today_str() -> str:
    return date.today().isoformat()

def now_iso() -> str:
    # 秒までで十分（必要ならミリ秒も可）
    return datetime.now().replace(microsecond=0).isoformat(sep=" ")

def parse_dt(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    # stored format: "YYYY-MM-DD HH:MM:SS"
    return datetime.fromisoformat(s)

def calc_work_minutes(clock_in: Optional[str], clock_out: Optional[str]) -> Optional[int]:
    dt_in = parse_dt(clock_in)
    dt_out = parse_dt(clock_out)
    if not dt_in or not dt_out:
        return None
    minutes = int((dt_out - dt_in).total_seconds() // 60)
    return max(minutes, 0)

def fmt_minutes(m: Optional[int]) -> str:
    if m is None:
        return "-"
    h = m // 60
    mm = m % 60
    return f"{h}:{mm:02d}"

# -------------------------
# routes
# -------------------------
@app.get("/")
def index():
    d = today_str()
    db = get_db()
    row = db.execute("SELECT * FROM work_logs WHERE work_date = ?", (d,)).fetchone()
    return render_template("index.html", today=d, row=row)

@app.post("/clock-in")
def clock_in():
    d = today_str()
    db = get_db()
    row = db.execute("SELECT * FROM work_logs WHERE work_date = ?", (d,)).fetchone()

    if row and row["clock_in"]:
        flash("すでに出勤打刻があります。", "warning")
        return redirect(url_for("index"))

    ts = now_iso()
    if row is None:
        db.execute(
            "INSERT INTO work_logs(work_date, clock_in, clock_out) VALUES (?, ?, NULL)",
            (d, ts),
        )
    else:
        db.execute("UPDATE work_logs SET clock_in = ? WHERE work_date = ?", (ts, d))
    db.commit()

    flash(f"出勤打刻しました：{ts}", "success")
    return redirect(url_for("index"))

@app.post("/clock-out")
def clock_out():
    d = today_str()
    db = get_db()
    row = db.execute("SELECT * FROM work_logs WHERE work_date = ?", (d,)).fetchone()

    if row is None or not row["clock_in"]:
        flash("先に出勤打刻をしてください。", "error")
        return redirect(url_for("index"))

    if row["clock_out"]:
        flash("すでに退勤打刻があります。", "warning")
        return redirect(url_for("index"))

    ts = now_iso()
    db.execute("UPDATE work_logs SET clock_out = ? WHERE work_date = ?", (ts, d))
    db.commit()

    flash(f"退勤打刻しました：{ts}", "success")
    return redirect(url_for("index"))

@app.get("/logs")
def logs():
    month = request.args.get("month")  # e.g. "2026-01"
    db = get_db()

    if month:
        # SQLite で YYYY-MM の前方一致
        rows = db.execute(
            "SELECT * FROM work_logs WHERE work_date LIKE ? ORDER BY work_date DESC",
            (f"{month}-%",),
        ).fetchall()
    else:
        rows = db.execute("SELECT * FROM work_logs ORDER BY work_date DESC").fetchall()

    enriched = []
    total_min = 0
    total_counted = 0

    for r in rows:
        mins = calc_work_minutes(r["clock_in"], r["clock_out"])
        if mins is not None:
            total_min += mins
            total_counted += 1
        enriched.append(
            {
                "work_date": r["work_date"],
                "clock_in": r["clock_in"] or "-",
                "clock_out": r["clock_out"] or "-",
                "work_time": fmt_minutes(mins),
            }
        )

    return render_template(
        "logs.html",
        rows=enriched,
        month=month or "",
        total_time=fmt_minutes(total_min if total_counted > 0 else None),
        total_days=total_counted,
    )

if __name__ == "__main__":
    app.run(debug=True)
