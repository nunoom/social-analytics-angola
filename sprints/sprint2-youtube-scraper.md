# Sprint 2 — Scraper YouTube + Coleta Real de Dados

**Projecto:** Plataforma de Analytics para Redes Sociais  
**Duração estimada:** 1–2 semanas  
**Pré-requisito:** Sprint 1 concluído (Docker + PostgreSQL a funcionar)  
**Objectivo:** Pipeline de coleta automática que recolhe vídeos e comentários do YouTube sobre tópicos relevantes (tecnologia, IA, Angola) e salva tudo no PostgreSQL.

---

## Estado actual da YouTube Data API v3 (2026)

Antes de começar, é importante conhecer as regras actuais:

| Operação | Custo em unidades |
|---|---|
| Leitura de vídeo / canal | 1 unidade |
| Pesquisa (`search`) | **100 unidades** |
| Comentários | 1 unidade |
| Escrita / upload | 50–100 unidades |

**Quota diária gratuita: 10.000 unidades por projecto Google Cloud.**  
Reset diário à meia-noite (hora do Pacífico).

> ⚠️ **Atenção ao search:** cada chamada de pesquisa custa 100 unidades. Com 10.000 unidades diárias, só tens **100 pesquisas por dia**. A estratégia do scraper vai respeitar isso rigorosamente.

---

## O que vais aprender neste sprint

| Tecnologia / Conceito | O que é |
|---|---|
| YouTube Data API v3 | API oficial do Google para dados públicos do YouTube |
| Google Cloud Console | Plataforma para gerir credenciais e projectos Google |
| `google-api-python-client` | Biblioteca Python oficial para APIs Google |
| Rate limiting | Como respeitar limites de APIs sem ser bloqueado |
| Retries com backoff | Como lidar com falhas de rede de forma elegante |
| APScheduler | Agendamento de tarefas automáticas em Python |
| Quota tracking | Como monitorizar o consumo de unidades da API |

---

## Passo 1 — Criar credenciais no Google Cloud Console

### 1.1 — Criar projecto

1. Vai a [console.cloud.google.com](https://console.cloud.google.com)
2. Clica em **"Select a project"** → **"New Project"**
3. Nome: `social-analytics-angola`
4. Clica em **Create**

### 1.2 — Activar a YouTube Data API

1. No menu lateral, vai a **APIs & Services → Library**
2. Pesquisa por `YouTube Data API v3`
3. Clica na API → **Enable**

### 1.3 — Criar a API Key

1. Vai a **APIs & Services → Credentials**
2. Clica em **+ Create Credentials → API Key**
3. Copia a chave gerada
4. Clica em **Edit API Key** e em **API restrictions**, selecciona **YouTube Data API v3** (boa prática de segurança)

### 1.4 — Adicionar ao `.env`

```env
# Adiciona ao ficheiro .env existente
YOUTUBE_API_KEY=AIzaSy_COLOCA_A_TUA_CHAVE_AQUI
```

> 💡 Ao contrário do Reddit, o YouTube não precisa de OAuth para dados públicos. Uma simples API Key chega para recolher vídeos, comentários e métricas públicas.

---

## Passo 2 — Actualizar a base de dados

Adiciona as novas tabelas ao ficheiro `infra/init.sql` (ou corre directamente no pgAdmin):

```sql
-- Tabela de canais YouTube
CREATE TABLE IF NOT EXISTS youtube_channels (
    id              SERIAL PRIMARY KEY,
    channel_id      VARCHAR(100) UNIQUE NOT NULL,
    title           VARCHAR(255),
    description     TEXT,
    subscriber_count BIGINT,
    video_count     INTEGER,
    view_count      BIGINT,
    country         VARCHAR(10),
    collected_at    TIMESTAMP DEFAULT NOW()
);

-- Tabela de vídeos YouTube
CREATE TABLE IF NOT EXISTS youtube_videos (
    id              SERIAL PRIMARY KEY,
    video_id        VARCHAR(100) UNIQUE NOT NULL,
    channel_id      VARCHAR(100),
    title           TEXT NOT NULL,
    description     TEXT,
    published_at    TIMESTAMP,
    view_count      BIGINT DEFAULT 0,
    like_count      INTEGER DEFAULT 0,
    comment_count   INTEGER DEFAULT 0,
    duration        VARCHAR(20),        -- formato ISO 8601 ex: PT4M13S
    tags            TEXT[],             -- array de tags do vídeo
    topic           VARCHAR(100),       -- tópico que originou a pesquisa
    collected_at    TIMESTAMP DEFAULT NOW()
);

-- Tabela de comentários YouTube
CREATE TABLE IF NOT EXISTS youtube_comments (
    id              SERIAL PRIMARY KEY,
    comment_id      VARCHAR(100) UNIQUE NOT NULL,
    video_id        VARCHAR(100) REFERENCES youtube_videos(video_id),
    author          VARCHAR(200),
    text            TEXT NOT NULL,
    like_count      INTEGER DEFAULT 0,
    published_at    TIMESTAMP,
    collected_at    TIMESTAMP DEFAULT NOW()
);

-- Tabela de controlo de quota diária
CREATE TABLE IF NOT EXISTS api_quota_log (
    id              SERIAL PRIMARY KEY,
    api_name        VARCHAR(50) NOT NULL,   -- 'youtube', 'reddit', etc.
    units_used      INTEGER NOT NULL,
    operation       VARCHAR(100),
    logged_at       TIMESTAMP DEFAULT NOW()
);

-- Índices para performance
CREATE INDEX IF NOT EXISTS idx_videos_topic ON youtube_videos(topic);
CREATE INDEX IF NOT EXISTS idx_videos_published ON youtube_videos(published_at);
CREATE INDEX IF NOT EXISTS idx_comments_video ON youtube_comments(video_id);
CREATE INDEX IF NOT EXISTS idx_quota_log_date ON api_quota_log(logged_at, api_name);
```

Aplica as alterações sem reiniciar o container:

```bash
docker exec -i analytics_db psql -U analytics_user -d social_analytics < infra/init.sql
```

---

## Passo 3 — Instalar dependências Python

```bash
cd scraper
source venv/bin/activate   # ou venv\Scripts\activate no Windows

pip install google-api-python-client apscheduler tenacity
pip freeze > requirements.txt
```

| Biblioteca | Para que serve |
|---|---|
| `google-api-python-client` | Cliente oficial para APIs Google, incluindo YouTube |
| `apscheduler` | Agenda a coleta automática a cada X horas |
| `tenacity` | Retries automáticos com backoff exponencial |

---

## Passo 4 — Gestor de quota

Antes de fazer qualquer chamada à API, precisas de controlar as unidades gastas para não exceder o limite diário. Cria `scraper/quota_manager.py`:

```python
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

DAILY_LIMIT = 9500  # Deixa 500 unidades de margem de segurança


def get_units_used_today(conn) -> int:
    """Devolve o total de unidades gastas hoje."""
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
    """Devolve um resumo do estado da quota."""
    used = get_units_used_today(conn)
    return {
        "used": used,
        "limit": DAILY_LIMIT,
        "remaining": DAILY_LIMIT - used,
        "percentage": round(used / DAILY_LIMIT * 100, 1),
    }
```

---

## Passo 5 — Scraper YouTube principal

Cria `scraper/youtube_scraper.py`:

```python
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
```

---

## Passo 6 — Actualizar o `config.py`

Adiciona a chave do YouTube ao ficheiro de configuração existente:

```python
# Adiciona ao scraper/config.py

YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY")
```

---

## Passo 7 — Correr e verificar

Executa o scraper:

```bash
cd scraper
source venv/bin/activate
python youtube_scraper.py
```

Deverás ver no terminal:

```
2026-06-01 14:30:01 [INFO] Quota YouTube: 0/9500 unidades usadas (0%) | Restam: 9500
2026-06-01 14:30:01 [INFO] --- Processando tópico: 'inteligência artificial Angola' ---
2026-06-01 14:30:01 [INFO] Pesquisando vídeos: 'inteligência artificial Angola'
2026-06-01 14:30:03 [INFO] Encontrados 10 vídeos para 'inteligência artificial Angola'
2026-06-01 14:30:04 [INFO] ✓ 10 vídeos guardados para 'inteligência artificial Angola'
...
╔══════════════════════════════════╗
║        Coleta Concluída          ║
╠══════════════════════════════════╣
║  Vídeos guardados:  58            ║
║  Comentários:       743           ║
║  Quota usada hoje:  627           ║
║  Quota restante:    8873          ║
╚══════════════════════════════════╝
```

Verifica os dados no pgAdmin:

```sql
-- Vídeos recolhidos por tópico
SELECT topic, COUNT(*) as total_videos, AVG(view_count) as media_views
FROM youtube_videos
GROUP BY topic
ORDER BY total_videos DESC;

-- Top 10 vídeos mais vistos
SELECT title, view_count, like_count, comment_count, topic
FROM youtube_videos
ORDER BY view_count DESC
LIMIT 10;

-- Comentários mais populares
SELECT c.text, c.like_count, v.title as video_title
FROM youtube_comments c
JOIN youtube_videos v ON c.video_id = v.video_id
ORDER BY c.like_count DESC
LIMIT 10;

-- Quota usada hoje
SELECT operation, SUM(units_used) as total_units, COUNT(*) as num_calls
FROM api_quota_log
WHERE api_name = 'youtube' AND logged_at::date = CURRENT_DATE
GROUP BY operation;
```

---

## Passo 8 — Agendamento automático com APScheduler

Para a coleta correr automaticamente a cada 6 horas, cria `scraper/scheduler.py`:

```python
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.interval import IntervalTrigger
from youtube_scraper import run_youtube_collection
import logging

log = logging.getLogger(__name__)

TOPICS = [
    "inteligência artificial Angola",
    "programação Python tutorial português",
    "tecnologia África",
    "machine learning engenharia de dados",
    "startups Angola 2025",
    "Java Spring Boot tutorial",
]

def job_collect():
    log.info("⏰ Coleta automática iniciada...")
    run_youtube_collection(TOPICS, videos_per_topic=8)
    log.info("✅ Coleta automática concluída.")


if __name__ == "__main__":
    scheduler = BlockingScheduler()

    # Corre a cada 6 horas
    # Com 6 tópicos × 100 unidades = 600 unidades por coleta
    # 4 coletas por dia = 2400 unidades → muito dentro do limite de 10.000
    scheduler.add_job(
        job_collect,
        trigger=IntervalTrigger(hours=6),
        id="youtube_collection",
        name="Coleta YouTube",
        replace_existing=True,
    )

    log.info("Scheduler iniciado. Coleta a cada 6 horas.")
    log.info("Ctrl+C para parar.")

    # Corre imediatamente uma vez ao iniciar
    job_collect()

    scheduler.start()
```

Corre o scheduler em background:

```bash
python scheduler.py &
```

---

## Gestão de quota — estratégia resumida

Com 10.000 unidades diárias e os custos actuais:

| O que fas | Custo | Vezes por dia |
|---|---|---|
| Pesquisa por tópico | 100 unidades | máx. 90 pesquisas |
| Detalhes de 50 vídeos | 1 unidade | praticamente ilimitado |
| 100 comentários | ~5 unidades | praticamente ilimitado |
| **6 tópicos × 4 coletas** | ~2.500 unidades | ✅ dentro do limite |

> 💡 **Regra de ouro:** minimiza `search`, maximiza `videos.list`. Uma pesquisa devolve IDs, e depois um único `videos.list` recolhe detalhes de 50 vídeos de uma só vez por apenas 1 unidade.

---

## Conceitos para aprofundar

### APIs e autenticação
- Diferença entre **API Key** e **OAuth 2.0** — quando usar cada uma?
- O que é um **rate limit** e como o backoff exponencial ajuda?
- O que significa **idempotência** e porque o `ON CONFLICT DO UPDATE` é importante aqui?

### Python avançado
- O que faz o decorador `@retry` da biblioteca `tenacity`?
- O que é **backoff exponencial**? (espera 2s, 4s, 8s... antes de tentar de novo)
- Como funciona um **context manager** (`with conn.cursor() as cur`)?

### PostgreSQL
- O que é um `JOIN` e como o usas para ligar `youtube_comments` a `youtube_videos`?
- O que faz `ON CONFLICT ... DO UPDATE`? (upsert — inserir ou actualizar)
- Como funcionam os **arrays** no PostgreSQL (`TEXT[]` para as tags)?

---

## Checklist do Sprint 2

- [ ] Projecto criado no Google Cloud Console
- [ ] YouTube Data API v3 activada
- [ ] API Key gerada e adicionada ao `.env`
- [ ] Novas tabelas criadas na base de dados (youtube_videos, youtube_comments, api_quota_log)
- [ ] `youtube_scraper.py` corre sem erros
- [ ] Dados aparecem nas queries de verificação no pgAdmin
- [ ] Quota a ser registada na tabela `api_quota_log`
- [ ] Scheduler configurado (opcional neste sprint)

---

## Próximo passo — Sprint 3

No Sprint 3 (ETL), vais:

- Criar um pipeline que **limpa e normaliza** os dados recolhidos
- **Extrair hashtags e tópicos** dos títulos e comentários
- Calcular **métricas de tendências** (crescimento, frequência, score)
- Detectar os temas mais falados nos vídeos sobre Angola e tecnologia
- Popular a tabela `hashtag_metrics` que criaste no Sprint 1

---

*Guia criado para o projecto: Plataforma de Analytics para Redes Sociais — Angola*  
*Stack: Python · YouTube Data API v3 · PostgreSQL · Docker · Kafka · Spring Boot*
