import os
from dotenv import load_dotenv
from sqlalchemy import create_engine, URL

load_dotenv()

database_url = URL.create(
    drivername="postgresql+psycopg",
    username=os.getenv("POSTGRES_USER", "flame_user"),
    password=os.getenv("POSTGRES_PASSWORD"),
    host="localhost",
    port=5432,
    database="flame_db",
)

engine = create_engine(database_url)
