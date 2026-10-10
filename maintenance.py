"""
The once-a-day job. Vercel Cron calls /cron/daily (see vercel.json).

It does three things:
1. Touches the database, so Supabase's free plan never pauses it for
   inactivity (it pauses projects that sit idle for a week).
2. Takes an automatic restore point of every sheet that changed since its
   last one — each instructor's own daily backup, which they can download
   or roll back to from their sheet's page.
3. Clears out what's no longer needed: rate-limit rows, expired codes,
   unconfirmed uploads, old error logs, and deleted sheets past their
   30-day undo window.
"""

from datetime import timedelta

import canvas_import
import db
import deadlines
import sheets
import signin
import usage
from util import iso, now


def run_daily():
    changed = db.rows(
        """
        SELECT s.id FROM sheets s
        WHERE s.updated_at > COALESCE(
            (SELECT MAX(n.created_at) FROM snapshots n WHERE n.sheet_id = s.id), ''
        )
        """
    )
    for found in changed:
        sheet = sheets.get_sheet(found["id"])
        if sheet:
            sheets.take_snapshot(sheet, "daily", "Automatic daily backup")
    db.commit()

    cutoff = lambda days: iso(now() - timedelta(days=days))  # noqa: E731
    db.run("DELETE FROM email_log WHERE sent_at < :t", t=cutoff(2))
    db.run("DELETE FROM login_codes WHERE expires_at < :t", t=cutoff(1))
    db.run("DELETE FROM login_links WHERE expires_at < :t", t=iso(now()))
    db.run("DELETE FROM auth_failures WHERE at < :t", t=cutoff(1))
    db.run("DELETE FROM pending_uploads WHERE created_at < :t", t=cutoff(1))
    db.run("DELETE FROM canvas_imports WHERE created_at < :t", t=iso(now() - canvas_import.KEEP))
    db.run("DELETE FROM error_log WHERE created_at < :t", t=cutoff(30))
    # Sign-ins last at most 90 days, so older sign-out markers do nothing.
    db.run("DELETE FROM student_signouts WHERE at < :t", t=cutoff(100))
    db.run(
        "DELETE FROM snapshots WHERE kind = 'deleted' AND created_at < :t",
        t=cutoff(sheets.DELETED_SHEETS_KEPT_DAYS),
    )
    # A deleted sheet's other restore points go when its undo window does.
    db.run(
        """
        DELETE FROM snapshots WHERE sheet_id NOT IN (SELECT id FROM sheets)
          AND sheet_id NOT IN (SELECT sheet_id FROM snapshots WHERE kind = 'deleted')
        """
    )
    db.commit()
    return {
        "sheets": db.scalar("SELECT COUNT(*) FROM sheets") or 0,
        "snapshots_taken": len(changed),
        "near_limits": usage.daily_check(),
        "workos_records_deleted": signin.forget_remote_users(),
        "deadlines_acted_on": deadlines.enforce_all(),
    }
