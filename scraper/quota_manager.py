import psycopg2
from datetime import date
from config import DB_CONFIG
import logging

log = logging.getLogger(__name__)

# Custo de cada operação em unidades
QUOTA_COSTS = {
    "search":           100,
    "videos.list":      1,
    "commentThreads":   1,
    "channels.list":    1,
}

DAILY_LIMIT = 9500

def get_units_used_today(conn) -> int:
    sql = """
        SELECT COALESCE(SUM(units_used), 0)
        FROM api_quota_log
        WHERE api_name = 'youtube'
            AND logged_at::date = CURRENT_DATE;
    """
    with conn.cursor() as cur:
        cur.execute(sql)
        return cur.fetchone()[0]

def log_quota_usage(conn, operation: str, units: int):
    """Regista o consumo de quota na base de dados."""
    sql = """
        INSERT INTO api_quota_log (api_name, units_used, operation)
        VALUES ('youtube', %s, %s);
    """
    with conn.cursor() as cur:
        cur.execute(sql, (units, operation))
    conn.commit()

def can_make_request(conn, operation: str) -> bool:
    """Verifica se há quota suficiente para a operação."""
    cost = QUOTA_COSTS.get(operation, 1)
    used = get_units_used_today(conn)
    remaining = DAILY_LIMIT - used

    if remaining < cost:
        log.warning(
            f"Quota insuficiente para '{operation}'. "
            f"Usadas: {used}/{DAILY_LIMIT} unidades hoje."
        )
        return False

    return True

def get_quota_status(conn) -> dict:
    """Devolv um resumo do estado da quota."""
    used = get_units_used_today(conn)
    return {
        "used": used,
        "limit": DAILY_LIMIT,
        "remaining": DAILY_LIMIT - used,
        "percentage": round(used / DAILY_LIMIT * 100, 1),
    }