# Sprint 1 — Fundação e Ambiente

**Projecto:** Plataforma de Analytics para Redes Sociais  
**Duração estimada:** 1–2 semanas  
**Nível:** Iniciante a intermediário  
**Objectivo:** Ter o ambiente Docker rodando com PostgreSQL, estrutura de pastas do projecto definida, e o primeiro script Python a recolher dados reais do Reddit e a salvá-los na base de dados.

---

## O que vais aprender neste sprint

| Tecnologia | Conceito |
|---|---|
| Docker + docker-compose | Containerização e infraestrutura como código |
| PostgreSQL | Modelação de dados, DDL, SQL básico |
| Python + psycopg2 | Ligação à base de dados e inserção de dados |
| PRAW | Reddit API — autenticação OAuth e recolha de posts |
| APScheduler *(opcional)* | Agendamento de tarefas automáticas |

---

## Pré-requisitos

Antes de começar, instala estas ferramentas na tua máquina:

- **Docker Desktop** — [docker.com/get-started](https://www.docker.com/get-started)
- **Python 3.10+** — [python.org](https://www.python.org)
- **VS Code** (recomendado) com as extensões: Python, Docker, PostgreSQL
- **Git** — para versionar o projecto desde o início

Verifica as instalações:

```bash
docker --version
python --version
git --version
```

---

## Passo 1 — Estrutura de pastas do projecto

Cria a estrutura base do projecto. Esta organização vai crescer contigo ao longo dos sprints.

```bash
mkdir social-analytics-angola
cd social-analytics-angola

mkdir scraper etl api dashboard infra
touch docker-compose.yml README.md .env .gitignore
```

Estrutura final:

```
social-analytics-angola/
├── scraper/              # Scripts Python de recolha de dados
│   ├── reddit_scraper.py
│   ├── requirements.txt
│   └── config.py
├── etl/                  # Pipeline de limpeza e transformação
├── api/                  # Spring Boot (Sprint 5)
├── dashboard/            # Frontend (Sprint 6)
├── infra/                # SQL, migrações, scripts de BD
│   └── init.sql
├── docker-compose.yml
├── .env                  # Variáveis de ambiente (NÃO commitar)
└── README.md
```

Adiciona ao `.gitignore`:

```
.env
__pycache__/
*.pyc
venv/
node_modules/
```

---

## Passo 2 — Docker Compose com PostgreSQL

O Docker permite correr o PostgreSQL sem instalá-lo directamente na tua máquina. Tudo fica isolado num container.

Edita o `docker-compose.yml`:

```yaml
version: '3.8'

services:
  postgres:
    image: postgres:15
    container_name: analytics_db
    environment:
      POSTGRES_DB: ${POSTGRES_DB}
      POSTGRES_USER: ${POSTGRES_USER}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
    ports:
      - "5432:5432"
    volumes:
      - postgres_data:/var/lib/postgresql/data
      - ./infra/init.sql:/docker-entrypoint-initdb.d/init.sql
    restart: unless-stopped

  pgadmin:
    image: dpage/pgadmin4
    container_name: analytics_pgadmin
    environment:
      PGADMIN_DEFAULT_EMAIL: admin@analytics.local
      PGADMIN_DEFAULT_PASSWORD: ${PGADMIN_PASSWORD}
    ports:
      - "5050:80"
    depends_on:
      - postgres
    restart: unless-stopped

volumes:
  postgres_data:
```

Cria o ficheiro `.env` com as variáveis:

```env
POSTGRES_DB=social_analytics
POSTGRES_USER=analytics_user
POSTGRES_PASSWORD=senha_segura_aqui
PGADMIN_PASSWORD=admin123

REDDIT_CLIENT_ID=coloca_aqui
REDDIT_CLIENT_SECRET=coloca_aqui
REDDIT_USER_AGENT=social-analytics-angola/1.0
```

> ⚠️ **Importante:** O ficheiro `.env` nunca deve ir para o Git. Contém credenciais sensíveis.

Arranca os containers:

```bash
docker compose up -d
```

Verifica que estão a correr:

```bash
docker compose ps
```

Acede ao pgAdmin em `http://localhost:5050` com o email e password do `.env`.

---

## Passo 3 — Modelação da base de dados

O ficheiro `infra/init.sql` é executado automaticamente quando o container do PostgreSQL arranca pela primeira vez.

```sql
-- Tabela de posts brutos recolhidos
CREATE TABLE IF NOT EXISTS raw_posts (
    id              SERIAL PRIMARY KEY,
    platform        VARCHAR(20) NOT NULL,           -- 'reddit', 'youtube', etc.
    external_id     VARCHAR(100) UNIQUE NOT NULL,   -- ID original da plataforma
    title           TEXT,
    body            TEXT,
    author          VARCHAR(100),
    url             TEXT,
    score           INTEGER DEFAULT 0,              -- upvotes, likes, views
    num_comments    INTEGER DEFAULT 0,
    subreddit       VARCHAR(100),                   -- específico do Reddit
    collected_at    TIMESTAMP DEFAULT NOW(),
    raw_json        JSONB                           -- dados completos originais
);

-- Tabela de hashtags/tópicos detectados
CREATE TABLE IF NOT EXISTS hashtags (
    id              SERIAL PRIMARY KEY,
    tag             VARCHAR(200) NOT NULL,
    platform        VARCHAR(20) NOT NULL,
    post_id         INTEGER REFERENCES raw_posts(id),
    detected_at     TIMESTAMP DEFAULT NOW()
);

-- Tabela de métricas diárias por hashtag
CREATE TABLE IF NOT EXISTS hashtag_metrics (
    id              SERIAL PRIMARY KEY,
    tag             VARCHAR(200) NOT NULL,
    platform        VARCHAR(20) NOT NULL,
    metric_date     DATE NOT NULL,
    mention_count   INTEGER DEFAULT 0,
    total_score     INTEGER DEFAULT 0,
    avg_score       DECIMAL(10,2) DEFAULT 0,
    UNIQUE(tag, platform, metric_date)
);

-- Índices para consultas rápidas
CREATE INDEX IF NOT EXISTS idx_raw_posts_platform ON raw_posts(platform);
CREATE INDEX IF NOT EXISTS idx_raw_posts_collected_at ON raw_posts(collected_at);
CREATE INDEX IF NOT EXISTS idx_hashtags_tag ON hashtags(tag);
CREATE INDEX IF NOT EXISTS idx_hashtag_metrics_date ON hashtag_metrics(metric_date);
```

> **O que aprendes aqui:** DDL (Data Definition Language), tipos de dados, chaves estrangeiras, índices para performance, e a coluna `raw_json JSONB` — uma forma poderosa do PostgreSQL guardar dados semi-estruturados.

---

## Passo 4 — Credenciais da API do Reddit

O Reddit oferece uma API gratuita e generosa para developers.

**Como criar a aplicação Reddit:**

1. Vai a [reddit.com/prefs/apps](https://www.reddit.com/prefs/apps)
2. Clica em **"Create App"** no fundo da página
3. Preenche:
   - **Name:** `social-analytics-angola`
   - **Type:** selecciona **script**
   - **Redirect URI:** `http://localhost:8080`
4. Clica em **Create app**
5. Copia o **Client ID** (texto abaixo do nome da app) e o **Client Secret**
6. Cola os valores no teu `.env`

---

## Passo 5 — Ambiente Python e dependências

Cria um ambiente virtual e instala as dependências:

```bash
cd scraper
python -m venv venv

# Activar no Linux/Mac:
source venv/bin/activate

# Activar no Windows:
venv\Scripts\activate

pip install praw psycopg2-binary python-dotenv
pip freeze > requirements.txt
```

Cria o ficheiro `scraper/config.py`:

```python
import os
from dotenv import load_dotenv

load_dotenv()  # Carrega as variáveis do .env

# Reddit
REDDIT_CLIENT_ID = os.getenv("REDDIT_CLIENT_ID")
REDDIT_CLIENT_SECRET = os.getenv("REDDIT_CLIENT_SECRET")
REDDIT_USER_AGENT = os.getenv("REDDIT_USER_AGENT", "social-analytics/1.0")

# PostgreSQL
DB_CONFIG = {
    "host": "localhost",
    "port": 5432,
    "dbname": os.getenv("POSTGRES_DB", "social_analytics"),
    "user": os.getenv("POSTGRES_USER"),
    "password": os.getenv("POSTGRES_PASSWORD"),
}
```

---

## Passo 6 — Primeiro scraper do Reddit

Cria `scraper/reddit_scraper.py`:

```python
import praw
import psycopg2
import json
import logging
from datetime import datetime
from config import REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET, REDDIT_USER_AGENT, DB_CONFIG

# Configurar logging para ver o que está a acontecer
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
log = logging.getLogger(__name__)


def get_reddit_client():
    """Cria e devolve um cliente Reddit autenticado."""
    return praw.Reddit(
        client_id=REDDIT_CLIENT_ID,
        client_secret=REDDIT_CLIENT_SECRET,
        user_agent=REDDIT_USER_AGENT,
    )


def get_db_connection():
    """Cria e devolve uma ligação ao PostgreSQL."""
    return psycopg2.connect(**DB_CONFIG)


def save_post(conn, post_data: dict) -> bool:
    """
    Salva um post na base de dados.
    Devolve True se inseriu, False se já existia (duplicado).
    """
    sql = """
        INSERT INTO raw_posts
            (platform, external_id, title, body, author, url, score, num_comments, subreddit, raw_json)
        VALUES
            (%(platform)s, %(external_id)s, %(title)s, %(body)s, %(author)s, %(url)s,
             %(score)s, %(num_comments)s, %(subreddit)s, %(raw_json)s)
        ON CONFLICT (external_id) DO NOTHING
        RETURNING id;
    """
    with conn.cursor() as cur:
        cur.execute(sql, post_data)
        result = cur.fetchone()
        conn.commit()
        return result is not None  # True = inserido, False = duplicado


def collect_subreddit(reddit, subreddit_name: str, limit: int = 25):
    """
    Recolhe os posts mais populares de um subreddit.
    """
    log.info(f"Recolhendo posts de r/{subreddit_name}...")
    subreddit = reddit.subreddit(subreddit_name)
    posts = []

    for post in subreddit.hot(limit=limit):
        # Ignorar posts fixos (pinned/stickied)
        if post.stickied:
            continue

        post_data = {
            "platform": "reddit",
            "external_id": post.id,
            "title": post.title,
            "body": post.selftext[:5000] if post.selftext else None,  # limitar tamanho
            "author": str(post.author) if post.author else "[deleted]",
            "url": f"https://reddit.com{post.permalink}",
            "score": post.score,
            "num_comments": post.num_comments,
            "subreddit": subreddit_name,
            "raw_json": json.dumps({
                "upvote_ratio": post.upvote_ratio,
                "flair": post.link_flair_text,
                "created_utc": post.created_utc,
            }),
        }
        posts.append(post_data)

    return posts


def run_collection(subreddits: list[str]):
    """
    Função principal — recolhe e salva dados de vários subreddits.
    """
    reddit = get_reddit_client()
    conn = get_db_connection()

    total_inserted = 0
    total_skipped = 0

    try:
        for sub in subreddits:
            posts = collect_subreddit(reddit, sub, limit=25)

            for post in posts:
                inserted = save_post(conn, post)
                if inserted:
                    total_inserted += 1
                    log.info(f"✓ Guardado: [{sub}] {post['title'][:60]}...")
                else:
                    total_skipped += 1

    finally:
        conn.close()

    log.info(f"\nRecolha concluída: {total_inserted} inseridos, {total_skipped} duplicados ignorados.")


if __name__ == "__main__":
    # Subreddits relevantes para começar
    # Inclui tópicos de tecnologia e África para ter contexto local
    SUBREDDITS = [
        "programming",
        "Python",
        "datascience",
        "artificial",
        "africa",
        "Angola",
    ]

    run_collection(SUBREDDITS)
```

Corre o scraper:

```bash
python reddit_scraper.py
```

Deves ver no terminal algo como:

```
2026-05-29 10:23:01 [INFO] Recolhendo posts de r/programming...
2026-05-29 10:23:03 [INFO] ✓ Guardado: [programming] Why Rust is replacing C in system...
2026-05-29 10:23:03 [INFO] ✓ Guardado: [programming] I built a real-time data pipeline wi...
...
2026-05-29 10:23:15 [INFO] Recolha concluída: 87 inseridos, 0 duplicados ignorados.
```

---

## Passo 7 — Verificar dados na base de dados

Abre o pgAdmin em `http://localhost:5050`, liga-te ao servidor PostgreSQL e corre estas queries para confirmar que os dados chegaram:

```sql
-- Quantos posts foram recolhidos?
SELECT COUNT(*) FROM raw_posts;

-- Ver os 10 posts mais populares
SELECT platform, subreddit, title, score, num_comments
FROM raw_posts
ORDER BY score DESC
LIMIT 10;

-- Posts por subreddit
SELECT subreddit, COUNT(*) as total
FROM raw_posts
GROUP BY subreddit
ORDER BY total DESC;
```

---

## O que acabaste de construir

```
[Reddit API] → [Python Scraper] → [PostgreSQL]
      ↑               ↑                ↑
   PRAW + OAuth    psycopg2 +       DDL + índices
                   tratamento       + JSONB
                   de erros
```

Esta é a base de tudo. Nos sprints seguintes, vais transformar estes dados brutos em tendências, métricas e insights.

---

## Conceitos para aprofundar (estudo recomendado)

### Docker e containers
- O que é um container vs uma máquina virtual?
- O que faz o `volumes:` no docker-compose? (persistência de dados)
- Diferença entre `image:` e `build:` no docker-compose

**Recursos:** Documentação oficial Docker, vídeo "Docker in 100 Seconds" — Fireship

### PostgreSQL e SQL
- Diferença entre `PRIMARY KEY` e `UNIQUE`
- O que faz `ON CONFLICT (external_id) DO NOTHING`? (idempotência)
- O que é `JSONB` e porque o PostgreSQL o suporta nativamente?
- Como funcionam os índices e quando os usar?

**Prática:** Experimenta as queries de agregação (`GROUP BY`, `COUNT`, `SUM`, `AVG`) na tua própria base de dados.

### Python e APIs
- O que é OAuth 2.0 e porque o Reddit o usa?
- O que é rate limiting e como o PRAW o gere automaticamente?
- O que é idempotência e porque o `ON CONFLICT DO NOTHING` é importante?

---

## Checklist do Sprint 1

Antes de avançar para o Sprint 2, confirma que consegues responder "sim" a tudo:

- [ ] `docker compose up -d` funciona sem erros
- [ ] pgAdmin está acessível em `http://localhost:5050`
- [ ] As tabelas `raw_posts`, `hashtags` e `hashtag_metrics` existem na BD
- [ ] O script Python corre sem erros e insere dados
- [ ] Consegues ver os dados no pgAdmin com uma query `SELECT`
- [ ] O projecto está versionado no Git com o primeiro commit
- [ ] O `.env` está no `.gitignore` e nunca foi commitado

---

## Próximo passo — Sprint 2

No Sprint 2, vais:

- Adicionar o scraper do **YouTube** com a Google API
- Implementar **rate limiting** e **retries** robustos
- Criar um **scheduler** com APScheduler para recolha automática a cada hora
- Começar a detectar e extrair **hashtags** dos textos dos posts

---

*Guia criado para o projecto: Plataforma de Analytics para Redes Sociais — Angola*  
*Stack: Python · PostgreSQL · Docker · Kafka · Spring Boot*
