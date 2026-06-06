-- Tabela de posts brutos recolhidos
CREATE TABLE IF NOT EXISTS raw_posts (
    id          SERIAL PRIMARY KEY,
    plataform           VARCHAR(20) NOT NULL,
    external_id         VARCHAR(100) UNIQUE NOT NULL,
    title               TEXT,
    body                TEXT,
    author              VARCHAR(100),
    url                 TEXT,
    score               INTEGER DEFAULT 0,
    num_comments        INTEGER DEFAULT 0,
    subreddit           VARCHAR(100),
    collected_at        TIMESTAMP DEFAULT NOW(),
    raw_json            JSONB
);

-- Tabela de hashtags/tópicos detectados
CREATE TABLE IF NOT EXISTS hashtags (
    id                  SERIAL PRIMARY KEY,
    tag                 VARCHAR(200) NOT NULL,
    plataform           VARCHAR(20) NOT NULL,
    post_id             INTEGER REFERENCES raw_posts(id),
    detected_at         TIMESTAMP DEFAULT NOW()
);

-- Tabela de métricas diárias por hashtag
CREATE TABLE IF NOT EXISTS hashtags_metrics (
    id                  SERIAL PRIMARY KEY,
    tag                 VARCHAR(200) NOT NULL,
    plataform           VARCHAR(20) NOT NULL,
    metric_date         DATE NOT NULL,
    mention_count       INTEGER DEFAULT 0,
    total_score         INTEGER DEFAULT 0,
    avg_score           DECIMAL(10,2) DEFAULT 0,
    UNIQUE(tag, plataform, metric_date)
);

-- Índices para consultas rápidas reddit
CREATE INDEX IF NOT EXISTS idx_raw_posts_plataform ON raw_posts(plataform);
CREATE INDEX IF NOT EXISTS idx_raw_posts_collected_at ON raw_posts(collected_at);
CREATE INDEX IF NOT EXISTS idx_hashtags_tag ON hashtags(tag);
CREATE INDEX IF NOT EXISTS idx_hashtags_metrics_date ON hashtags_metrics(metric_date);

-- Tabela de canais YouTube
CREATE TABLE IF NOT EXISTS youtube_channels (
    id                  SERIAL PRIMARY KEY,
    channel_id          VARCHAR(100) UNIQUE NOT NULL,
    title               VARCHAR(255),
    description         TEXT,
    subscriber_count    BIGINT,
    video_count         INTEGER,
    view_count          BIGINT,
    country             VARCHAR(10),
    collected_at        TIMESTAMP DEFAULT NOW()
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