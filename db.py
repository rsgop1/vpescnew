"""Async PostgreSQL database layer using asyncpg with automatic SQLite fallback."""
import sys
import os

_LIBS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "python_libs")
if os.path.isdir(_LIBS_DIR) and _LIBS_DIR not in sys.path:
    sys.path.insert(0, _LIBS_DIR)

try:
    import asyncpg
except ImportError:
    asyncpg = None
import aiosqlite
import json
import logging
import os
import re
import uuid
from datetime import datetime, timezone
from config import DATABASE_URL, TELEGRAM_GROUP_ID

log = logging.getLogger(__name__)

_pool = None
_sqlite_conn = None


class SqliteRecord(dict):
    def __getitem__(self, key):
        if isinstance(key, int):
            return list(self.values())[key]
        return super().__getitem__(key)
    def get(self, key, default=None):
        return super().get(key, default)


class SqliteConn:
    def __init__(self, db):
        self.db = db

    def _convert_sql_and_params(self, sql, args):
        # Strip PostgreSQL locking and null ordering keywords unsupported by SQLite
        c_sql = re.sub(r'\bFOR UPDATE SKIP LOCKED\b', '', sql, flags=re.IGNORECASE)
        c_sql = re.sub(r'\bFOR UPDATE\b', '', c_sql, flags=re.IGNORECASE)
        c_sql = re.sub(r'\bNULLS FIRST\b', '', c_sql, flags=re.IGNORECASE)
        c_sql = re.sub(r'\bNULLS LAST\b', '', c_sql, flags=re.IGNORECASE)
        c_sql = c_sql.replace('ADD COLUMN IF NOT EXISTS', 'ADD COLUMN')
        c_sql = re.sub(r'\bTRUE\b', '1', c_sql)
        c_sql = re.sub(r'\bFALSE\b', '0', c_sql)

        # Convert Postgres $1, $2 to SQLite named parameters :p1, :p2 so repeated placeholders map correctly
        if re.search(r'\$\d+', c_sql):
            c_sql = re.sub(r'\$(\d+)', r':p\1', c_sql)
            params = {f"p{i+1}": a for i, a in enumerate(args)}
            return c_sql, params
        return c_sql, args

    async def execute(self, sql, *args):
        c_sql, params = self._convert_sql_and_params(sql, args)
        try:
            return await self.db.execute(c_sql, params)
        except Exception as e:
            err = str(e).lower()
            if 'duplicate column' in err or 'already exists' in err:
                return None
            log.warning(f"SQLite execute note on [{c_sql[:60]}]: {e}")
            raise

    async def fetch(self, sql, *args):
        c_sql, params = self._convert_sql_and_params(sql, args)
        async with self.db.execute(c_sql, params) as cur:
            rows = await cur.fetchall()
            cols = [d[0] for d in cur.description] if cur.description else []
            return [SqliteRecord(zip(cols, row)) for row in rows]

    async def fetchrow(self, sql, *args):
        rows = await self.fetch(sql, *args)
        return rows[0] if rows else None

    async def fetchval(self, sql, *args):
        c_sql, params = self._convert_sql_and_params(sql, args)
        async with self.db.execute(c_sql, params) as cur:
            row = await cur.fetchone()
            return row[0] if row else None

    class _Tx:
        def __init__(self, db):
            self.db = db
        async def __aenter__(self):
            await self.db.execute('BEGIN IMMEDIATE')
            return self
        async def __aexit__(self, exc_type, exc_val, exc_tb):
            if exc_type:
                await self.db.execute('ROLLBACK')
            else:
                await self.db.execute('COMMIT')

    def transaction(self):
        return self._Tx(self.db)


class SqlitePool:
    def __init__(self, db):
        self.db = db
    class _Acquire:
        def __init__(self, db):
            self.db = db
        async def __aenter__(self):
            return SqliteConn(self.db)
        async def __aexit__(self, *args):
            pass
    def acquire(self):
        return self._Acquire(self.db)


async def get_pool():
    global _pool, _sqlite_conn
    if _pool is not None:
        return _pool

    # Try connecting to PostgreSQL if DATABASE_URL is configured and not default unroutable loopback
    if DATABASE_URL and "127.0.0.1" not in DATABASE_URL and asyncpg is not None:
        try:
            _pool = await asyncpg.create_pool(DATABASE_URL, min_size=2, max_size=10)
            log.info("Connected to PostgreSQL database pool")
            return _pool
        except Exception as e:
            log.warning(f"PostgreSQL connection failed ({e}). Falling back to local SQLite.")

    if _sqlite_conn is None:
        db_path = os.path.join(os.path.dirname(__file__), "vaultpay.db")
        _sqlite_conn = await aiosqlite.connect(db_path, timeout=30.0, isolation_level=None)
        await _sqlite_conn.execute("PRAGMA journal_mode=WAL")
        await _sqlite_conn.execute("PRAGMA synchronous=NORMAL")
        _pool = SqlitePool(_sqlite_conn)
        log.info(f"Initialized persistent SQLite database (WAL mode) at {db_path}")

    return _pool


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def gen_id() -> str:
    return str(uuid.uuid4())


async def init_db():
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            tg_id BIGINT PRIMARY KEY,
            username TEXT DEFAULT '',
            balance_vc INTEGER DEFAULT 0,
            locked_vc INTEGER DEFAULT 0,
            total_deals INTEGER DEFAULT 0,
            total_volume INTEGER DEFAULT 0,
            rating_sum REAL DEFAULT 0,
            rating_count INTEGER DEFAULT 0,
            vouches INTEGER DEFAULT 0,
            scam_flag BOOLEAN DEFAULT FALSE,
            frozen BOOLEAN DEFAULT FALSE,
            badges TEXT DEFAULT '[]',
            referral_code TEXT DEFAULT '',
            referred_by BIGINT DEFAULT NULL,
            tier TEXT DEFAULT 'new',
            daily_withdrawn INTEGER DEFAULT 0,
            last_withdrawal_date TEXT DEFAULT '',
            notify_enabled BOOLEAN DEFAULT TRUE,
            created_at TEXT DEFAULT ''
        )""")
        await conn.execute("""
        CREATE TABLE IF NOT EXISTS deals (
            deal_id TEXT PRIMARY KEY,
            chat_id BIGINT,
            buyer_id BIGINT,
            buyer_un TEXT DEFAULT '',
            seller_id BIGINT,
            seller_un TEXT DEFAULT '',
            amount INTEGER DEFAULT 0,
            fee_payer TEXT DEFAULT 'buyer',
            fee_percent REAL DEFAULT 1.0,
            toc TEXT DEFAULT '',
            role TEXT DEFAULT 'buy',
            status TEXT DEFAULT 'awaiting_acceptance',
            seller_pin TEXT DEFAULT '',
            message_id BIGINT,
            created_at TEXT DEFAULT '',
            expires_at TEXT DEFAULT '',
            paid_at TEXT,
            released_at TEXT,
            cancelled BOOLEAN DEFAULT FALSE,
            locked_amount INTEGER DEFAULT 0,
            seller_credit INTEGER DEFAULT 0,
            fee_collected INTEGER DEFAULT 0,
            buyer_accepted BOOLEAN DEFAULT FALSE,
            seller_accepted BOOLEAN DEFAULT FALSE
        )""")
        await conn.execute("""
        CREATE TABLE IF NOT EXISTS transactions (
            id TEXT PRIMARY KEY,
            type TEXT,
            user_id BIGINT,
            deal_id TEXT DEFAULT '',
            amount INTEGER DEFAULT 0,
            status TEXT DEFAULT 'pending',
            data_json TEXT DEFAULT '{}',
            at TEXT DEFAULT ''
        )""")
        await conn.execute("""
        CREATE TABLE IF NOT EXISTS disputes (
            id TEXT PRIMARY KEY,
            deal_id TEXT,
            opened_by BIGINT,
            status TEXT DEFAULT 'open',
            messages_json TEXT DEFAULT '[]',
            opened_at TEXT DEFAULT '',
            resolved_at TEXT DEFAULT '',
            resolution TEXT DEFAULT '',
            resolved_by TEXT DEFAULT '',
            ai_verdict_json TEXT DEFAULT '',
            ai_resolved_auto BOOLEAN DEFAULT FALSE,
            ai_checked_at TEXT DEFAULT '',
            ai_pending_from TEXT DEFAULT '[]',
            ai_asked_questions TEXT DEFAULT '[]',
            escalated BOOLEAN DEFAULT FALSE,
            evidence_deadline TEXT DEFAULT '',
            appeal_used BOOLEAN DEFAULT FALSE,
            jury_json TEXT DEFAULT '',
            appeal_verdict_json TEXT DEFAULT '',
            ticket_code TEXT DEFAULT ''
        )""")
        # Backfill columns for disputes tables created before the AI judge feature existed.
        await conn.execute("ALTER TABLE disputes ADD COLUMN IF NOT EXISTS ai_verdict_json TEXT DEFAULT ''")
        await conn.execute("ALTER TABLE disputes ADD COLUMN IF NOT EXISTS ai_resolved_auto BOOLEAN DEFAULT FALSE")
        await conn.execute("ALTER TABLE disputes ADD COLUMN IF NOT EXISTS ai_checked_at TEXT DEFAULT ''")
        await conn.execute("ALTER TABLE disputes ADD COLUMN IF NOT EXISTS ai_pending_from TEXT DEFAULT '[]'")
        await conn.execute("ALTER TABLE disputes ADD COLUMN IF NOT EXISTS ai_asked_questions TEXT DEFAULT '[]'")
        await conn.execute("ALTER TABLE disputes ADD COLUMN IF NOT EXISTS escalated BOOLEAN DEFAULT FALSE")
        await conn.execute("ALTER TABLE disputes ADD COLUMN IF NOT EXISTS evidence_deadline TEXT DEFAULT ''")
        await conn.execute("ALTER TABLE disputes ADD COLUMN IF NOT EXISTS appeal_used BOOLEAN DEFAULT FALSE")
        await conn.execute("ALTER TABLE disputes ADD COLUMN IF NOT EXISTS jury_json TEXT DEFAULT ''")
        await conn.execute("ALTER TABLE disputes ADD COLUMN IF NOT EXISTS appeal_verdict_json TEXT DEFAULT ''")
        await conn.execute("ALTER TABLE disputes ADD COLUMN IF NOT EXISTS ticket_code TEXT DEFAULT ''")
        await conn.execute("""
        CREATE TABLE IF NOT EXISTS blacklist (
            id TEXT PRIMARY KEY,
            value TEXT,
            type TEXT DEFAULT 'other',
            reason TEXT DEFAULT '',
            at TEXT DEFAULT '',
            by TEXT DEFAULT ''
        )""")
        await conn.execute("""
        CREATE TABLE IF NOT EXISTS groups_tbl (
            chat_id BIGINT PRIMARY KEY,
            title TEXT DEFAULT '',
            active BOOLEAN DEFAULT TRUE,
            added_by TEXT DEFAULT '',
            at TEXT DEFAULT ''
        )""")
        if TELEGRAM_GROUP_ID:
            await conn.execute(
                """INSERT INTO groups_tbl (chat_id, title, active, added_by, at)
                   VALUES ($1, 'Configured primary deal group', TRUE, 'system', $2)
                   ON CONFLICT (chat_id) DO NOTHING""",
                TELEGRAM_GROUP_ID, now_iso(),
            )
        await conn.execute("""
        CREATE TABLE IF NOT EXISTS audit_log (
            id TEXT PRIMARY KEY,
            actor TEXT DEFAULT '',
            action TEXT DEFAULT '',
            details_json TEXT DEFAULT '{}',
            at TEXT DEFAULT ''
        )""")
        await conn.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT DEFAULT '',
            updated_at TEXT DEFAULT ''
        )""")
        await conn.execute("""
        CREATE TABLE IF NOT EXISTS vouches (
            id TEXT PRIMARY KEY,
            from_id BIGINT,
            to_id BIGINT,
            text TEXT DEFAULT '',
            at TEXT DEFAULT ''
        )""")
        await conn.execute("""
        CREATE TABLE IF NOT EXISTS referrals (
            id TEXT PRIMARY KEY,
            referrer BIGINT,
            referred BIGINT,
            bonus_paid BOOLEAN DEFAULT FALSE,
            at TEXT DEFAULT ''
        )""")
        await conn.execute("""
        CREATE TABLE IF NOT EXISTS broadcasts (
            id TEXT PRIMARY KEY,
            text TEXT DEFAULT '',
            by TEXT DEFAULT '',
            count INTEGER DEFAULT 0,
            at TEXT DEFAULT ''
        )""")
        await conn.execute("""
        CREATE TABLE IF NOT EXISTS deal_templates (
            id TEXT PRIMARY KEY,
            name TEXT DEFAULT '',
            toc TEXT DEFAULT '',
            amount INTEGER DEFAULT 0,
            by TEXT DEFAULT '',
            at TEXT DEFAULT ''
        )""")
        await conn.execute("""
        CREATE TABLE IF NOT EXISTS treasury (
            id TEXT PRIMARY KEY DEFAULT 'main',
            balance INTEGER DEFAULT 0
        )""")
        await conn.execute("""
        CREATE TABLE IF NOT EXISTS deal_counter (
            id TEXT PRIMARY KEY DEFAULT 'deal',
            seq INTEGER DEFAULT 4091
        )""")
        await conn.execute("""
        CREATE TABLE IF NOT EXISTS scam_reports (
            id TEXT PRIMARY KEY,
            reporter_id BIGINT,
            target_id BIGINT,
            target_un TEXT DEFAULT '',
            reason TEXT DEFAULT '',
            deal_id TEXT DEFAULT '',
            at TEXT DEFAULT '',
            status TEXT DEFAULT 'pending'
        )""")
        await conn.execute("""
        CREATE TABLE IF NOT EXISTS broadcast_templates (
            id TEXT PRIMARY KEY,
            name TEXT DEFAULT '',
            text TEXT DEFAULT '',
            by TEXT DEFAULT '',
            at TEXT DEFAULT ''
        )""")
        await conn.execute("""
        CREATE TABLE IF NOT EXISTS middlemen (
            tg_id BIGINT PRIMARY KEY,
            username TEXT DEFAULT '',
            active BOOLEAN DEFAULT TRUE,
            added_at TEXT DEFAULT '',
            added_by TEXT DEFAULT ''
        )""")
        await conn.execute("""
        CREATE TABLE IF NOT EXISTS middleman_rooms (
            chat_id BIGINT PRIMARY KEY,
            title TEXT DEFAULT '',
            status TEXT DEFAULT 'available',
            deal_id TEXT DEFAULT '',
            registered_at TEXT DEFAULT '',
            updated_at TEXT DEFAULT ''
        )""")
        await conn.execute("""
        CREATE TABLE IF NOT EXISTS pending_media_purges (
            id TEXT PRIMARY KEY,
            chat_id BIGINT,
            message_id BIGINT,
            purge_at TEXT DEFAULT '',
            ref_id TEXT DEFAULT '',
            deal_id TEXT DEFAULT ''
        )""")
        # Add new columns to deals/vouches if not present
        for col_sql in [
            "ALTER TABLE deals ADD COLUMN IF NOT EXISTS payment_method TEXT DEFAULT 'wallet'",
            "ALTER TABLE deals ADD COLUMN IF NOT EXISTS middleman_id BIGINT DEFAULT NULL",
            "ALTER TABLE deals ADD COLUMN IF NOT EXISTS middleman_un TEXT DEFAULT ''",
            "ALTER TABLE deals ADD COLUMN IF NOT EXISTS product_name TEXT DEFAULT ''",
            "ALTER TABLE deals ADD COLUMN IF NOT EXISTS nudged_at TEXT DEFAULT NULL",
            "ALTER TABLE vouches ADD COLUMN IF NOT EXISTS status TEXT DEFAULT 'approved'",
            "ALTER TABLE vouches ADD COLUMN IF NOT EXISTS proof_json TEXT DEFAULT '[]'",
            # Auto-Hold Guardian — mid-deal AI suspicion can pause release without a formal dispute
            "ALTER TABLE deals ADD COLUMN IF NOT EXISTS on_hold BOOLEAN DEFAULT FALSE",
            "ALTER TABLE deals ADD COLUMN IF NOT EXISTS hold_reason TEXT DEFAULT ''",
            "ALTER TABLE deals ADD COLUMN IF NOT EXISTS held_at TEXT DEFAULT ''",
            # Identity Risk Check — recorded once at deal creation for admin visibility
            "ALTER TABLE deals ADD COLUMN IF NOT EXISTS risk_score INTEGER DEFAULT 0",
            "ALTER TABLE deals ADD COLUMN IF NOT EXISTS risk_notes TEXT DEFAULT ''",
            "ALTER TABLE deals ADD COLUMN IF NOT EXISTS room_chat_id BIGINT DEFAULT NULL",
            "ALTER TABLE deals ADD COLUMN IF NOT EXISTS room_message_id BIGINT DEFAULT NULL",
            "ALTER TABLE deals ADD COLUMN IF NOT EXISTS room_buyer_joined BOOLEAN DEFAULT FALSE",
            "ALTER TABLE deals ADD COLUMN IF NOT EXISTS room_seller_joined BOOLEAN DEFAULT FALSE",
            "ALTER TABLE deals ADD COLUMN IF NOT EXISTS room_middleman_joined BOOLEAN DEFAULT FALSE",
            "ALTER TABLE deals ADD COLUMN IF NOT EXISTS room_buyer_confirmed BOOLEAN DEFAULT FALSE",
            "ALTER TABLE deals ADD COLUMN IF NOT EXISTS room_seller_confirmed BOOLEAN DEFAULT FALSE",
            "ALTER TABLE deals ADD COLUMN IF NOT EXISTS room_middleman_confirmed BOOLEAN DEFAULT FALSE",
            "ALTER TABLE deals ADD COLUMN IF NOT EXISTS room_transcript_json TEXT DEFAULT '[]'",
            "ALTER TABLE deals ADD COLUMN IF NOT EXISTS room_ready_to_proceed BOOLEAN DEFAULT FALSE",
            "ALTER TABLE deals ADD COLUMN IF NOT EXISTS room_chat_locked BOOLEAN DEFAULT TRUE",
            "ALTER TABLE deals ADD COLUMN IF NOT EXISTS room_disputed_at TEXT DEFAULT ''",
            "ALTER TABLE deals ADD COLUMN IF NOT EXISTS room_confirmation_prompted BOOLEAN DEFAULT FALSE",
            "ALTER TABLE deals ADD COLUMN IF NOT EXISTS room_action_msg_id BIGINT DEFAULT NULL",
            "ALTER TABLE deals ADD COLUMN IF NOT EXISTS mm_upi TEXT DEFAULT ''",
            "ALTER TABLE deals ADD COLUMN IF NOT EXISTS seller_upi TEXT DEFAULT ''",
            "ALTER TABLE deals ADD COLUMN IF NOT EXISTS mm_type TEXT DEFAULT 'manual'",
            # Abuse/profanity tracking — feeds AI judge credibility scoring + repeat-offender flags
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS abuse_warnings INTEGER DEFAULT 0",
            "ALTER TABLE disputes ADD COLUMN IF NOT EXISTS resolution_note TEXT DEFAULT ''",
            "ALTER TABLE deals ADD COLUMN IF NOT EXISTS cancelled_at TEXT DEFAULT ''",
        ]:
            try:
                await conn.execute(col_sql)
            except Exception:
                pass
        # Unique index on disputes.deal_id so ON CONFLICT works
        try:
            await conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS disputes_deal_id_uidx ON disputes(deal_id)"
            )
        except Exception:
            pass
        # Insert defaults
        await conn.execute(
            "INSERT INTO treasury (id, balance) VALUES ('main', 0) ON CONFLICT (id) DO NOTHING"
        )
        await conn.execute(
            "INSERT INTO deal_counter (id, seq) VALUES ('deal', 4091) ON CONFLICT (id) DO NOTHING"
        )


def row_to_dict(row) -> dict:
    if row is None:
        return None
    return dict(row)


def rows_to_list(rows) -> list:
    return [dict(r) for r in rows]


# ─── User operations ───────────────────────────────────────────────────────────

async def get_or_create_user(tg_id: int, username: str = None) -> dict:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM users WHERE tg_id = $1", tg_id)
        if row:
            u = row_to_dict(row)
            if username and u.get('username') != username:
                await conn.execute("UPDATE users SET username = $1 WHERE tg_id = $2", username, tg_id)
                u['username'] = username
            return u
        u = {
            'tg_id': tg_id,
            'username': username or '',
            'balance_vc': 0, 'locked_vc': 0,
            'total_deals': 0, 'total_volume': 0,
            'rating_sum': 0.0, 'rating_count': 0,
            'vouches': 0, 'scam_flag': False, 'frozen': False,
            'badges': '[]',
            'referral_code': f'REF{tg_id}',
            'referred_by': None,
            'tier': 'new',
            'daily_withdrawn': 0,
            'last_withdrawal_date': '',
            'notify_enabled': True,
            'created_at': now_iso(),
        }
        await conn.execute("""
            INSERT INTO users (tg_id, username, balance_vc, locked_vc, total_deals, total_volume,
                rating_sum, rating_count, vouches, scam_flag, frozen, badges, referral_code,
                referred_by, tier, daily_withdrawn, last_withdrawal_date, notify_enabled, created_at)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19)
        """, tg_id, u['username'], 0, 0, 0, 0, 0.0, 0, 0, False, False,
             '[]', f'REF{tg_id}', None, 'new', 0, '', True, now_iso())
        return u


async def get_user(tg_id: int) -> dict | None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM users WHERE tg_id = $1", tg_id)
        return row_to_dict(row)


async def get_user_by_username(username: str) -> dict | None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM users WHERE LOWER(username) = LOWER($1)", username.lstrip("@")
        )
        return row_to_dict(row)


async def update_user(tg_id: int, **kwargs):
    if not kwargs:
        return
    pool = await get_pool()
    sets = ', '.join(f"{k} = ${i+2}" for i, k in enumerate(kwargs.keys()))
    vals = list(kwargs.values())
    async with pool.acquire() as conn:
        await conn.execute(f"UPDATE users SET {sets} WHERE tg_id = $1", tg_id, *vals)


async def adjust_balance(tg_id: int, delta_balance: int = 0, delta_locked: int = 0):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE users SET balance_vc = balance_vc + $1, locked_vc = locked_vc + $2 WHERE tg_id = $3",
            delta_balance, delta_locked, tg_id
        )


async def get_all_users(limit: int = 500) -> list:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT * FROM users ORDER BY total_volume DESC LIMIT $1", limit)
        return rows_to_list(rows)


# ─── Deal operations ───────────────────────────────────────────────────────────

async def next_deal_id() -> int:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "UPDATE deal_counter SET seq = seq + 1 WHERE id = 'deal' RETURNING seq"
        )
        return row['seq']


async def create_deal(deal: dict):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO deals (deal_id, chat_id, buyer_id, buyer_un, seller_id, seller_un,
                amount, fee_payer, fee_percent, toc, role, status, seller_pin, message_id,
                created_at, expires_at, paid_at, released_at, cancelled, locked_amount,
                seller_credit, fee_collected, buyer_accepted, seller_accepted, mm_type)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,$20,$21,$22,$23,$24,$25)
        """, deal['deal_id'], deal['chat_id'], deal.get('buyer_id'), deal['buyer_un'],
             deal.get('seller_id'), deal['seller_un'], deal['amount'], deal['fee_payer'],
             deal['fee_percent'], deal.get('toc', ''), deal['role'], deal['status'],
             deal['seller_pin'], deal.get('message_id'), deal['created_at'], deal['expires_at'],
             deal.get('paid_at'), deal.get('released_at'), False, 0, 0, 0,
             deal.get('buyer_accepted', False), deal.get('seller_accepted', False),
             deal.get('mm_type', 'manual'))


async def get_deal(deal_id: str) -> dict | None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM deals WHERE deal_id = $1", deal_id)
        return row_to_dict(row)


async def increment_abuse_warning(tg_id: int) -> int:
    """Bump a user's abuse/profanity warning counter and return the new total."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "UPDATE users SET abuse_warnings = abuse_warnings + 1 WHERE tg_id = $1 RETURNING abuse_warnings",
            tg_id,
        )
        return row["abuse_warnings"] if row else 1


async def update_deal(deal_id: str, **kwargs):
    if not kwargs:
        return
    pool = await get_pool()
    sets = ', '.join(f"{k} = ${i+2}" for i, k in enumerate(kwargs.keys()))
    vals = list(kwargs.values())
    async with pool.acquire() as conn:
        await conn.execute(f"UPDATE deals SET {sets} WHERE deal_id = $1", deal_id, *vals)


async def register_middleman_room(chat_id: int, title: str, status: str = "available"):
    """Register one private group as a reusable middleman room."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO middleman_rooms (chat_id, title, status, registered_at, updated_at)
               VALUES ($1,$2,$3,$4,$4)
               ON CONFLICT (chat_id) DO UPDATE SET title = EXCLUDED.title,
                   updated_at = EXCLUDED.updated_at""",
            chat_id, title or str(chat_id), status, now_iso(),
        )


async def get_middleman_room(chat_id: int) -> dict | None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM middleman_rooms WHERE chat_id = $1", chat_id)
        return row_to_dict(row)


async def claim_middleman_room(deal_id: str) -> dict | None:
    """Atomically reserve the first available room so two deals cannot share one room."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            existing = await conn.fetchrow(
                "SELECT * FROM middleman_rooms WHERE deal_id = $1", deal_id
            )
            if existing:
                return row_to_dict(existing)
            row = await conn.fetchrow(
                """SELECT * FROM middleman_rooms
                   WHERE status = 'available'
                      OR (status = 'quarantined' AND (deal_id = '' OR deal_id IS NULL))
                   ORDER BY CASE status WHEN 'available' THEN 0 ELSE 1 END, updated_at ASC NULLS FIRST
                   LIMIT 1 FOR UPDATE SKIP LOCKED"""
            )
            if not row:
                return None
            await conn.execute(
                "UPDATE middleman_rooms SET status='occupied', deal_id=$1, updated_at=$2 WHERE chat_id=$3",
                deal_id, now_iso(), row["chat_id"],
            )
            result = dict(row)
            result["status"] = "occupied"
            result["deal_id"] = deal_id
            return result


async def set_middleman_room_status(chat_id: int, status: str, deal_id: str = ""):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE middleman_rooms SET status=$1, deal_id=$2, updated_at=$3 WHERE chat_id=$4",
            status, deal_id or "", now_iso(), chat_id,
        )


async def get_middleman_rooms() -> list:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM middleman_rooms ORDER BY "
            "CASE status WHEN 'occupied' THEN 1 WHEN 'quarantined' THEN 2 ELSE 3 END, "
            "updated_at DESC NULLS LAST"
        )
        return rows_to_list(rows)


async def get_deal_by_room_chat(chat_id: int) -> dict | None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """SELECT * FROM deals
               WHERE (room_chat_id = $1 OR chat_id = $1)
                 AND status NOT IN ('cancelled','expired')
               ORDER BY created_at DESC LIMIT 1""",
            chat_id,
        )
        return row_to_dict(row)


async def append_room_transcript(deal_id: str, entry: dict):
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT room_transcript_json FROM deals WHERE deal_id = $1 FOR UPDATE", deal_id
        )
        if not row:
            return
        try:
            entries = json.loads(row["room_transcript_json"] or "[]")
        except Exception:
            entries = []
        entries.append(entry)
        await conn.execute(
            "UPDATE deals SET room_transcript_json=$1 WHERE deal_id=$2",
            json.dumps(entries[-1000:]), deal_id,
        )


async def get_deals(status: str = None, limit: int = 200) -> list:
    if status == "all":
        status = None
    pool = await get_pool()
    async with pool.acquire() as conn:
        if status:
            rows = await conn.fetch(
                "SELECT * FROM deals WHERE status = $1 ORDER BY created_at DESC LIMIT $2", status, limit
            )
        else:
            rows = await conn.fetch("SELECT * FROM deals ORDER BY created_at DESC LIMIT $1", limit)
        return rows_to_list(rows)


async def get_user_deals(tg_id: int) -> list:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM deals WHERE (buyer_id = $1 OR seller_id = $1) AND status NOT IN ('completed','cancelled') ORDER BY created_at DESC",
            tg_id
        )
        return rows_to_list(rows)


# ─── Transaction operations ────────────────────────────────────────────────────

async def create_transaction(tx: dict):
    pool = await get_pool()
    async with pool.acquire() as conn:
        tx_id = tx.get('id', gen_id())
        await conn.execute("""
            INSERT INTO transactions (id, type, user_id, deal_id, amount, status, data_json, at)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8)
        """, tx_id, tx['type'], tx.get('user_id'), tx.get('deal_id', ''),
             tx.get('amount', 0), tx.get('status', 'pending'),
             json.dumps(tx.get('data', {})), tx.get('at', now_iso()))
        return tx_id


async def get_transactions(tx_type: str = None, status: str = None, limit: int = 200) -> list:
    pool = await get_pool()
    async with pool.acquire() as conn:
        conds, vals = [], []
        if tx_type:
            conds.append(f"type = ${len(vals)+1}")
            vals.append(tx_type)
        if status:
            conds.append(f"status = ${len(vals)+1}")
            vals.append(status)
        where = f"WHERE {' AND '.join(conds)}" if conds else ""
        rows = await conn.fetch(
            f"SELECT * FROM transactions {where} ORDER BY at DESC LIMIT {limit}", *vals
        )
        return rows_to_list(rows)


async def get_transaction(tx_id: str) -> dict | None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM transactions WHERE id = $1", tx_id)
        return row_to_dict(row)


async def find_deposit_by_image_hash(image_hash: str, exclude_tx_id: str = "") -> dict | None:
    """Look for a prior deposit transaction (any status) whose screenshot hash matches — used to
    catch someone reusing the exact same proof-of-payment screenshot across multiple deposit claims."""
    if not image_hash:
        return None
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT * FROM transactions
            WHERE type = 'deposit' AND id != $1
              AND (data_json::jsonb ->> 'image_hash') = $2
            ORDER BY at ASC LIMIT 1
            """,
            exclude_tx_id, image_hash,
        )
        return row_to_dict(row)


async def update_transaction(tx_id: str, **kwargs):
    if not kwargs:
        return
    pool = await get_pool()
    sets = ', '.join(f"{k} = ${i+2}" for i, k in enumerate(kwargs.keys()))
    vals = list(kwargs.values())
    async with pool.acquire() as conn:
        await conn.execute(f"UPDATE transactions SET {sets} WHERE id = $1", tx_id, *vals)


# ─── Dispute operations ────────────────────────────────────────────────────────

async def create_dispute(dispute: dict):
    pool = await get_pool()
    async with pool.acquire() as conn:
        d_id = gen_id()
        ticket_code = dispute.get("ticket_code") or f"DP-{dispute['deal_id']}"
        await conn.execute("""
            INSERT INTO disputes (id, deal_id, opened_by, status, messages_json, opened_at, ticket_code)
            VALUES ($1,$2,$3,$4,$5,$6,$7)
            ON CONFLICT (deal_id) DO UPDATE SET status = 'open', opened_by = $3,
              opened_at = $6, ticket_code = COALESCE(NULLIF(disputes.ticket_code, ''), $7)
        """, d_id, dispute['deal_id'], dispute['opened_by'], 'open', '[]', now_iso(), ticket_code)
        return ticket_code


async def get_dispute(deal_id: str) -> dict | None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM disputes WHERE deal_id = $1", deal_id)
        if row:
            d = row_to_dict(row)
            try:
                d['messages'] = json.loads(d.get('messages_json', '[]'))
            except Exception:
                d['messages'] = []
            return d
        return None


async def add_dispute_message(deal_id: str, msg: dict):
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT messages_json FROM disputes WHERE deal_id = $1", deal_id)
        if row:
            msgs = json.loads(row['messages_json'] or '[]')
            msgs.append(msg)
            await conn.execute("UPDATE disputes SET messages_json = $1 WHERE deal_id = $2", json.dumps(msgs), deal_id)


async def get_disputes(status: str = None) -> list:
    pool = await get_pool()
    async with pool.acquire() as conn:
        if status:
            rows = await conn.fetch("SELECT * FROM disputes WHERE status = $1 ORDER BY opened_at DESC", status)
        else:
            rows = await conn.fetch("SELECT * FROM disputes ORDER BY opened_at DESC")
        result = []
        for r in rows:
            d = dict(r)
            try:
                d['messages'] = json.loads(d.get('messages_json', '[]'))
            except Exception:
                d['messages'] = []
            result.append(d)
        return result


async def update_dispute(deal_id: str, **kwargs):
    if not kwargs:
        return
    pool = await get_pool()
    sets = ', '.join(f"{k} = ${i+2}" for i, k in enumerate(kwargs.keys()))
    vals = list(kwargs.values())
    async with pool.acquire() as conn:
        await conn.execute(f"UPDATE disputes SET {sets} WHERE deal_id = $1", deal_id, *vals)


async def get_user_dispute_history(tg_id: int, exclude_deal_id: str = None) -> dict:
    """Cross-deal pattern signal: how often this user has been in a dispute and how it went."""
    if not tg_id:
        return {"total": 0, "resolved_against": 0, "resolved_favor": 0}
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT d.resolution, deals.buyer_id, deals.seller_id
            FROM disputes d JOIN deals ON deals.deal_id = d.deal_id
            WHERE d.status = 'resolved' AND (deals.buyer_id = $1 OR deals.seller_id = $1)
              AND ($2::text IS NULL OR d.deal_id != $2)
        """, tg_id, exclude_deal_id)
    total = len(rows)
    against = 0
    favor = 0
    for r in rows:
        was_seller = r['seller_id'] == tg_id
        if r['resolution'] == 'release_seller':
            if was_seller:
                favor += 1
            else:
                against += 1
        elif r['resolution'] == 'refund_buyer':
            if was_seller:
                against += 1
            else:
                favor += 1
    return {"total": total, "resolved_against": against, "resolved_favor": favor}


async def get_resolved_precedents(limit: int = 5) -> list:
    """Recent resolved disputes (with AI reasoning) to use as precedent examples for consistency."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT deal_id, resolution, resolved_by, ai_verdict_json
            FROM disputes WHERE status = 'resolved' AND ai_verdict_json != ''
            ORDER BY resolved_at DESC LIMIT $1
        """, limit)
    out = []
    for r in rows:
        try:
            v = json.loads(r['ai_verdict_json'])
        except Exception:
            continue
        out.append({
            "resolution": r['resolution'],
            "reasoning": v.get("reasoning", "")[:220],
        })
    return out


async def get_open_disputes_with_expired_deadline() -> list:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT deal_id FROM disputes
            WHERE status = 'open' AND evidence_deadline != ''
              AND evidence_deadline < $1
        """, now_iso())
    return [r['deal_id'] for r in rows]


# ─── Settings ─────────────────────────────────────────────────────────────────

async def get_setting(key: str, default=None):
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT value FROM settings WHERE key = $1", key)
        if not row:
            return default
        v = row['value']
        try:
            return json.loads(v)
        except Exception:
            return v


async def set_setting(key: str, value):
    pool = await get_pool()
    async with pool.acquire() as conn:
        v = json.dumps(value) if not isinstance(value, str) else value
        await conn.execute(
            "INSERT INTO settings (key, value, updated_at) VALUES ($1,$2,$3) ON CONFLICT (key) DO UPDATE SET value=$2, updated_at=$3",
            key, v, now_iso()
        )


async def get_media_storage_channel() -> int:
    val = await get_setting("media_storage_channel_id", None)
    if val is not None:
        try:
            return int(val)
        except (ValueError, TypeError):
            pass
    from config import MEDIA_STORAGE_CHANNEL_ID
    return MEDIA_STORAGE_CHANNEL_ID


async def set_media_storage_channel(channel_id: int):
    await set_setting("media_storage_channel_id", int(channel_id))


async def schedule_media_purge(chat_id: int, message_id: int, purge_at: str, ref_id: str = "", deal_id: str = ""):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO pending_media_purges (id, chat_id, message_id, purge_at, ref_id, deal_id) VALUES ($1, $2, $3, $4, $5, $6)",
            gen_id(), int(chat_id), int(message_id), str(purge_at), str(ref_id), str(deal_id)
        )


async def get_due_media_purges(cutoff_iso: str) -> list[dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM pending_media_purges WHERE purge_at <= $1 ORDER BY purge_at ASC LIMIT 100",
            cutoff_iso
        )
        return [row_to_dict(r) for r in rows]


async def remove_media_purge(purge_id: str):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM pending_media_purges WHERE id = $1", purge_id)


# ─── Treasury ─────────────────────────────────────────────────────────────────

async def get_treasury() -> int:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT balance FROM treasury WHERE id = 'main'")
        return int(row['balance']) if row else 0


async def add_to_treasury(amount: int):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE treasury SET balance = balance + $1 WHERE id = 'main'", amount
        )


async def subtract_from_treasury(amount: int):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE treasury SET balance = balance - $1 WHERE id = 'main'", amount
        )


# ─── Groups ───────────────────────────────────────────────────────────────────

async def is_group_whitelisted(chat_id: int, primary_group_id: int) -> bool:
    if primary_group_id and chat_id == primary_group_id:
        return True
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT active FROM groups_tbl WHERE chat_id = $1", chat_id)
        return bool(row and row['active'])


async def get_all_groups() -> list:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT * FROM groups_tbl ORDER BY at DESC")
        return rows_to_list(rows)


async def add_group(chat_id: int, title: str, added_by: str):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO groups_tbl (chat_id, title, active, added_by, at) VALUES ($1,$2,TRUE,$3,$4) ON CONFLICT (chat_id) DO UPDATE SET active=TRUE, title=$2",
            chat_id, title, added_by, now_iso()
        )


async def remove_group(chat_id: int):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("UPDATE groups_tbl SET active = FALSE WHERE chat_id = $1", chat_id)


# ─── Blacklist ────────────────────────────────────────────────────────────────

async def get_blacklist() -> list:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT * FROM blacklist ORDER BY at DESC")
        return rows_to_list(rows)


async def add_blacklist(value: str, bl_type: str, reason: str, by: str):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO blacklist (id, value, type, reason, at, by) VALUES ($1,$2,$3,$4,$5,$6)",
            gen_id(), value, bl_type, reason, now_iso(), by
        )


async def del_blacklist(value: str):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM blacklist WHERE value = $1", value)


async def is_blacklisted(value: str) -> bool:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT id FROM blacklist WHERE value = $1", value)
        return bool(row)


# ─── Audit log ────────────────────────────────────────────────────────────────

async def log_action(actor: str, action: str, details: dict = None):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO audit_log (id, actor, action, details_json, at) VALUES ($1,$2,$3,$4,$5)",
            gen_id(), actor, action, json.dumps(details or {}), now_iso()
        )


async def get_audit_log(limit: int = 200) -> list:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT * FROM audit_log ORDER BY at DESC LIMIT $1", limit)
        result = []
        for r in rows:
            d = dict(r)
            try:
                d['details'] = json.loads(d.get('details_json', '{}'))
            except Exception:
                d['details'] = {}
            result.append(d)
        return result


# ─── Vouches ──────────────────────────────────────────────────────────────────

async def add_vouch(from_id: int, to_id: int, text: str):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO vouches (id, from_id, to_id, text, at) VALUES ($1,$2,$3,$4,$5)",
            gen_id(), from_id, to_id, text, now_iso()
        )
        await conn.execute("UPDATE users SET vouches = vouches + 1 WHERE tg_id = $1", to_id)


async def get_vouches(to_id: int, limit: int = 20) -> list:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM vouches WHERE to_id = $1 ORDER BY at DESC LIMIT $2", to_id, limit
        )
        return rows_to_list(rows)


async def get_pending_vouches() -> list:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT v.*, u1.username as from_un, u2.username as to_un
            FROM vouches v
            LEFT JOIN users u1 ON u1.tg_id = v.from_id
            LEFT JOIN users u2 ON u2.tg_id = v.to_id
            WHERE v.status = 'pending'
            ORDER BY v.at DESC
        """)
        result = []
        for r in rows:
            d = dict(r)
            try:
                d['proofs'] = json.loads(d.get('proof_json', '[]'))
            except Exception:
                d['proofs'] = []
            result.append(d)
        return result


async def get_vouch_by_id(vouch_id: str) -> dict | None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM vouches WHERE id = $1", vouch_id)
        if row:
            d = dict(row)
            try:
                d['proofs'] = json.loads(d.get('proof_json', '[]'))
            except Exception:
                d['proofs'] = []
            return d
        return None


async def approve_vouch(vouch_id: str) -> dict | None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM vouches WHERE id = $1", vouch_id)
        if row:
            v = dict(row)
            if v.get('status') == 'pending':
                await conn.execute("UPDATE vouches SET status = 'approved' WHERE id = $1", vouch_id)
                await conn.execute(
                    "UPDATE users SET vouches = vouches + 1 WHERE tg_id = $1", v['to_id']
                )
            return v
        return None


async def reject_vouch(vouch_id: str) -> dict | None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM vouches WHERE id = $1", vouch_id)
        if row:
            await conn.execute("UPDATE vouches SET status = 'rejected' WHERE id = $1", vouch_id)
        return row_to_dict(row)


async def update_vouch_proof(vouch_id: str, proof_json: str):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE vouches SET proof_json = $1 WHERE id = $2", proof_json, vouch_id
        )


# ─── Referrals ────────────────────────────────────────────────────────────────

async def add_referral(referrer: int, referred: int):
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT id FROM referrals WHERE referred = $1", referred)
        if not row:
            await conn.execute(
                "INSERT INTO referrals (id, referrer, referred, bonus_paid, at) VALUES ($1,$2,$3,FALSE,$4)",
                gen_id(), referrer, referred, now_iso()
            )
            await conn.execute("UPDATE users SET referred_by = $1 WHERE tg_id = $2", referrer, referred)


async def get_referral_count(referrer: int) -> int:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT COUNT(*) as c FROM referrals WHERE referrer = $1", referrer)
        return int(row['c']) if row else 0


async def get_unpaid_referral(referred: int):
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM referrals WHERE referred = $1 AND bonus_paid = FALSE", referred
        )
        return row_to_dict(row)


async def mark_referral_paid(referred: int):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("UPDATE referrals SET bonus_paid = TRUE WHERE referred = $1", referred)


# ─── Templates ────────────────────────────────────────────────────────────────

async def get_templates() -> list:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT * FROM deal_templates ORDER BY at DESC")
        return rows_to_list(rows)


async def get_template(tmpl_id: str) -> dict | None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM deal_templates WHERE id = $1", tmpl_id)
        return row_to_dict(row)


async def add_template(name: str, toc: str, amount: int, by: str) -> str:
    pool = await get_pool()
    tmpl_id = gen_id()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO deal_templates (id, name, toc, amount, by, at) VALUES ($1,$2,$3,$4,$5,$6)",
            tmpl_id, name, toc, amount, by, now_iso()
        )
    return tmpl_id


async def del_template(tmpl_id: str):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM deal_templates WHERE id = $1", tmpl_id)


# ─── Scam reports ─────────────────────────────────────────────────────────────

async def add_scam_report(reporter_id: int, target_id: int, target_un: str, reason: str, deal_id: str = ''):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO scam_reports (id, reporter_id, target_id, target_un, reason, deal_id, at, status)
            VALUES ($1,$2,$3,$4,$5,$6,$7,'pending')
        """, gen_id(), reporter_id, target_id, target_un, reason, deal_id, now_iso())


async def get_scam_reports() -> list:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT * FROM scam_reports ORDER BY at DESC")
        return rows_to_list(rows)


# ─── Broadcast templates ──────────────────────────────────────────────────────

async def get_broadcast_templates() -> list:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT * FROM broadcast_templates ORDER BY at DESC")
        return rows_to_list(rows)


async def add_broadcast_template(name: str, text: str, by: str):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO broadcast_templates (id, name, text, by, at) VALUES ($1,$2,$3,$4,$5)",
            gen_id(), name, text, by, now_iso()
        )


async def del_broadcast_template(tmpl_id: str):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM broadcast_templates WHERE id = $1", tmpl_id)


# ─── Middlemen ────────────────────────────────────────────────────────────────

async def get_active_middlemen() -> list:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT * FROM middlemen WHERE active = TRUE ORDER BY added_at DESC")
        return rows_to_list(rows)


async def get_all_middlemen() -> list:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT * FROM middlemen ORDER BY added_at DESC")
        return rows_to_list(rows)


async def get_middleman(tg_id: int) -> dict | None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM middlemen WHERE tg_id = $1", tg_id)
        return row_to_dict(row)


async def is_middleman(tg_id: int) -> bool:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT tg_id FROM middlemen WHERE tg_id = $1 AND active = TRUE", tg_id)
        return bool(row)


async def add_middleman(tg_id: int, username: str, added_by: str):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO middlemen (tg_id, username, active, added_at, added_by)
            VALUES ($1, $2, TRUE, $3, $4)
            ON CONFLICT (tg_id) DO UPDATE SET username=$2, active=TRUE, added_by=$4
        """, tg_id, username, now_iso(), added_by)


async def toggle_middleman(tg_id: int, active: bool):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("UPDATE middlemen SET active=$1 WHERE tg_id=$2", active, tg_id)


async def delete_middleman(tg_id: int):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM middlemen WHERE tg_id = $1", tg_id)


# ─── Stats ────────────────────────────────────────────────────────────────────

async def get_stats() -> dict:
    pool = await get_pool()
    async with pool.acquire() as conn:
        total_vc_row = await conn.fetchrow("SELECT COALESCE(SUM(balance_vc + locked_vc), 0) AS total FROM users")
        total_users = await conn.fetchval("SELECT COUNT(*) FROM users")
        active_deals = await conn.fetchval(
            "SELECT COUNT(*) FROM deals WHERE status NOT IN ('completed','cancelled')"
        )
        total_deals = await conn.fetchval("SELECT COUNT(*) FROM deals")
        pending_dep = await conn.fetchval(
            "SELECT COUNT(*) FROM transactions WHERE type='deposit_request' AND status='pending'"
        )
        pending_wd = await conn.fetchval(
            "SELECT COUNT(*) FROM transactions WHERE type='withdraw_request' AND status='pending'"
        )
        open_disputes = await conn.fetchval("SELECT COUNT(*) FROM disputes WHERE status='open'")
        treasury_bal = await conn.fetchval("SELECT balance FROM treasury WHERE id='main'")
        
        # Revenue by day (last 7 days)
        revenue_rows = await conn.fetch("""
            SELECT LEFT(at, 10) as day, SUM(amount) as total 
            FROM transactions WHERE type='deal_release'
            GROUP BY LEFT(at, 10) ORDER BY day DESC LIMIT 7
        """)
        
        recent_deals = await conn.fetch(
            "SELECT * FROM deals ORDER BY created_at DESC LIMIT 10"
        )
        
        return {
            'total_vc_circulation': int(total_vc_row['total'] or 0),
            'treasury_balance': int(treasury_bal or 0),
            'active_deals': int(active_deals or 0),
            'total_deals': int(total_deals or 0),
            'total_users': int(total_users or 0),
            'pending_deposits': int(pending_dep or 0),
            'pending_withdrawals': int(pending_wd or 0),
            'open_disputes': int(open_disputes or 0),
            'revenue_chart': [dict(r) for r in revenue_rows],
            'recent_deals': rows_to_list(recent_deals),
        }
