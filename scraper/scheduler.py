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