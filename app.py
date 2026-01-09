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
            work_date TEXT NOT NULL,            -- YYYY-MM-DD
            clock_in TEXT,                      -- ISO datetime string
            clock_out TEXT,                     -- ISO datetime string
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    db.execute("CREATE INDEX IF NOT EXISTS idx_work_date ON work_logs(work_date);")
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
    rows = db.execute(
        "SELECT * FROM work_logs WHERE work_date = ? ORDER BY id",
        (d,)
    ).fetchall()
    
    # 最後の打刻レコードを取得
    last_record = rows[-1] if rows else None
    
    return render_template("index.html", today=d, records=rows, last_record=last_record)

@app.post("/clock-in")
def clock_in():
    d = today_str()
    db = get_db()
    
    # 最後のレコードを確認
    last = db.execute(
        "SELECT * FROM work_logs WHERE work_date = ? ORDER BY id DESC LIMIT 1",
        (d,)
    ).fetchone()
    
    # 最後のレコードが退勤済みでない場合は警告
    if last and not last["clock_out"]:
        flash("先に退勤打刻をしてください。", "warning")
        return redirect(url_for("index"))
    
    ts = now_iso()
    db.execute(
        "INSERT INTO work_logs(work_date, clock_in, clock_out) VALUES (?, ?, NULL)",
        (d, ts),
    )
    db.commit()
    
    flash(f"出勤打刻しました：{ts}", "success")
    return redirect(url_for("index"))

@app.post("/clock-out")
def clock_out():
    d = today_str()
    db = get_db()
    
    # 最後のレコードを確認
    last = db.execute(
        "SELECT * FROM work_logs WHERE work_date = ? ORDER BY id DESC LIMIT 1",
        (d,)
    ).fetchone()
    
    if not last or not last["clock_in"]:
        flash("先に出勤打刻をしてください。", "error")
        return redirect(url_for("index"))
    
    if last["clock_out"]:
        flash("すでに退勤打刻があります。新しい出勤打刻をしてください。", "warning")
        return redirect(url_for("index"))
    
    ts = now_iso()
    db.execute(
        "UPDATE work_logs SET clock_out = ? WHERE id = ?",
        (ts, last["id"])
    )
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
            "SELECT * FROM work_logs WHERE work_date LIKE ? ORDER BY work_date DESC, id",
            (f"{month}-%",),
        ).fetchall()
    else:
        rows = db.execute("SELECT * FROM work_logs ORDER BY work_date DESC, id").fetchall()

    # 日付ごとにグループ化
    daily_logs = {}
    for r in rows:
        d = r["work_date"]
        if d not in daily_logs:
            daily_logs[d] = []
        
        mins = calc_work_minutes(r["clock_in"], r["clock_out"])
        daily_logs[d].append({
            "clock_in": r["clock_in"] or "-",
            "clock_out": r["clock_out"] or "-",
            "work_time": fmt_minutes(mins),
            "minutes": mins or 0
        })
    
    # 各日の合計を計算
    enriched = []
    total_min = 0
    
    for d in sorted(daily_logs.keys(), reverse=True):
        day_total = sum(rec["minutes"] for rec in daily_logs[d])
        total_min += day_total
        
        enriched.append({
            "work_date": d,
            "records": daily_logs[d],
            "day_total": fmt_minutes(day_total)
        })

    return render_template(
        "logs.html",
        daily_logs=enriched,
        month=month or "",
        total_time=fmt_minutes(total_min),
        total_days=len(daily_logs),
    )

if __name__ == "__main__":
    app.run(debug=True)
