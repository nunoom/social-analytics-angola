import psycopg2
import logging
from datetime import datetime
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from config import DB_CONFIG, YOUTUBE_API_KEY
from quota_manager import can_make_request, log_quota_usage, get_quota_status, QUOTA_COSTS

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
log = logging.getLogger(__name__)


# ─── Cliente YouTube ────────────────────────────────────────────────

def get_youtube_client():
    return build("youtube", "v3", developerKey=YOUTUBE_API_KEY)


def get_db_connection():
    return psycopg2.connect(**DB_CONFIG)


# ─── Retry automático para erros de rede ────────────────────────────

@retry(
    retry=retry_if_exception_type(Exception),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    reraise=True
)
def safe_api_call(func, *args, **kwargs):
    """
    Executa uma chamada à API com retry automático.
    Se falhar 3 vezes, lança o erro.
    Backoff exponencial: espera 2s, 4s, 8s entre tentativas.
    """
    return func(*args, **kwargs)


# ─── Pesquisa de vídeos ─────────────────────────────────────────────

def search_videos(youtube, conn, query: str, max_results: int = 10) -> list[str]:
    """
    Pesquisa vídeos por query. Devolve lista de video_ids.
    Custo: 100 unidades por chamada.
    """
    if not can_make_request(conn, "search"):
        log.warning("Pesquisa cancelada: quota insuficiente.")
        return []

    log.info(f"Pesquisando vídeos: '{query}'")

    request = youtube.search().list(
        part="id,snippet",
        q=query,
        type="video",
        maxResults=max_results,
        order="relevance",
        relevanceLanguage="pt",     # prioriza português
        regionCode="AO",            # Angola como região preferencial
    )

    response = safe_api_call(request.execute)
    log_quota_usage(conn, "search", QUOTA_COSTS["search"])

    video_ids = []
    for item in response.get("items", []):
        if item["id"]["kind"] == "youtube#video":
            video_ids.append(item["id"]["videoId"])

    log.info(f"Encontrados {len(video_ids)} vídeos para '{query}'")
    return video_ids


# ─── Detalhes dos vídeos ────────────────────────────────────────────

def fetch_video_details(youtube, conn, video_ids: list[str], topic: str) -> list[dict]:
    """
    Recolhe métricas detalhadas de uma lista de vídeos.
    Custo: 1 unidade por chamada (independente do número de IDs).
    """
    if not video_ids:
        return []

    if not can_make_request(conn, "videos.list"):
        return []

    # A API aceita até 50 IDs por chamada — muito eficiente
    ids_str = ",".join(video_ids[:50])

    request = youtube.videos().list(
        part="snippet,statistics,contentDetails",
        id=ids_str,
    )

    response = safe_api_call(request.execute)
    log_quota_usage(conn, "videos.list", QUOTA_COSTS["videos.list"])

    videos = []
    for item in response.get("items", []):
        snippet = item.get("snippet", {})
        stats = item.get("statistics", {})
        content = item.get("contentDetails", {})

        videos.append({
            "video_id":      item["id"],
            "channel_id":    snippet.get("channelId"),
            "title":         snippet.get("title", ""),
            "description":   snippet.get("description", "")[:2000],
            "published_at":  snippet.get("publishedAt"),
            "view_count":    int(stats.get("viewCount", 0)),
            "like_count":    int(stats.get("likeCount", 0)),
            "comment_count": int(stats.get("commentCount", 0)),
            "duration":      content.get("duration", ""),
            "tags":          snippet.get("tags", []),
            "topic":         topic,
        })

    return videos


# ─── Comentários ────────────────────────────────────────────────────

def fetch_comments(youtube, conn, video_id: str, max_comments: int = 20) -> list[dict]:
    """
    Recolhe os comentários mais relevantes de um vídeo.
    Custo: 1 unidade por chamada.
    """
    if not can_make_request(conn, "commentThreads"):
        return []

    try:
        request = youtube.commentThreads().list(
            part="snippet",
            videoId=video_id,
            maxResults=max_comments,
            order="relevance",
            textFormat="plainText",
        )

        response = safe_api_call(request.execute)
        log_quota_usage(conn, "commentThreads", QUOTA_COSTS["commentThreads"])

    except HttpError as e:
        # Comentários podem estar desactivados no vídeo — não é erro crítico
        if "commentsDisabled" in str(e):
            log.debug(f"Comentários desactivados: {video_id}")
        else:
            log.warning(f"Erro ao recolher comentários de {video_id}: {e}")
        return []

    comments = []
    for item in response.get("items", []):
        top = item["snippet"]["topLevelComment"]["snippet"]
        comments.append({
            "comment_id":    item["id"],
            "video_id":      video_id,
            "author":        top.get("authorDisplayName", ""),
            "text":          top.get("textDisplay", "")[:1000],
            "like_count":    top.get("likeCount", 0),
            "published_at":  top.get("publishedAt"),
        })

    return comments


# ─── Salvar na base de dados ─────────────────────────────────────────

def save_videos(conn, videos: list[dict]) -> int:
    sql = """
        INSERT INTO youtube_videos
            (video_id, channel_id, title, description, published_at,
             view_count, like_count, comment_count, duration, tags, topic)
        VALUES
            (%(video_id)s, %(channel_id)s, %(title)s, %(description)s, %(published_at)s,
             %(view_count)s, %(like_count)s, %(comment_count)s, %(duration)s, %(tags)s, %(topic)s)
        ON CONFLICT (video_id) DO UPDATE SET
            view_count    = EXCLUDED.view_count,
            like_count    = EXCLUDED.like_count,
            comment_count = EXCLUDED.comment_count,
            collected_at  = NOW();
    """
    count = 0
    with conn.cursor() as cur:
        for v in videos:
            cur.execute(sql, v)
            count += 1
    conn.commit()
    return count


def save_comments(conn, comments: list[dict]) -> int:
    sql = """
        INSERT INTO youtube_comments
            (comment_id, video_id, author, text, like_count, published_at)
        VALUES
            (%(comment_id)s, %(video_id)s, %(author)s, %(text)s, %(like_count)s, %(published_at)s)
        ON CONFLICT (comment_id) DO NOTHING;
    """
    count = 0
    with conn.cursor() as cur:
        for c in comments:
            cur.execute(sql, c)
            count += 1
    conn.commit()
    return count


# ─── Função principal ────────────────────────────────────────────────

def run_youtube_collection(topics: list[str], videos_per_topic: int = 10):
    """
    Recolhe vídeos e comentários para uma lista de tópicos.
    Respeita os limites de quota automaticamente.
    """
    youtube = get_youtube_client()
    conn = get_db_connection()

    try:
        # Mostra estado da quota antes de começar
        status = get_quota_status(conn)
        log.info(
            f"Quota YouTube: {status['used']}/{status['limit']} unidades usadas "
            f"({status['percentage']}%) | Restam: {status['remaining']}"
        )

        total_videos = 0
        total_comments = 0

        for topic in topics:
            log.info(f"\n--- Processando tópico: '{topic}' ---")

            # 1. Pesquisa (100 unidades)
            video_ids = search_videos(youtube, conn, topic, max_results=videos_per_topic)

            if not video_ids:
                log.warning(f"Nenhum vídeo encontrado ou quota esgotada para '{topic}'")
                continue

            # 2. Detalhes dos vídeos (1 unidade)
            videos = fetch_video_details(youtube, conn, video_ids, topic)
            saved_videos = save_videos(conn, videos)
            total_videos += saved_videos
            log.info(f"✓ {saved_videos} vídeos guardados para '{topic}'")

            # 3. Comentários dos vídeos (1 unidade por vídeo)
            for video in videos:
                comments = fetch_comments(youtube, conn, video["video_id"], max_comments=15)
                saved = save_comments(conn, comments)
                total_comments += saved

        # Resumo final
        final_status = get_quota_status(conn)
        log.info(f"""
╔══════════════════════════════════╗
║        Coleta Concluída          ║
╠══════════════════════════════════╣
║  Vídeos guardados:  {total_videos:<13} ║
║  Comentários:       {total_comments:<13} ║
║  Quota usada hoje:  {final_status['used']:<13} ║
║  Quota restante:    {final_status['remaining']:<13} ║
╚══════════════════════════════════╝
        """)

    finally:
        conn.close()


# ─── Entrypoint ──────────────────────────────────────────────────────

if __name__ == "__main__":
    TOPICS = [
        "inteligência artificial Angola",
        "programação Python tutorial português",
        "tecnologia África",
        "machine learning engenharia de dados",
        "startups Angola 2025",
        "Java Spring Boot tutorial",
    ]

    run_youtube_collection(TOPICS, videos_per_topic=10)