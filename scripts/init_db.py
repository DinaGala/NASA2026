from sqlalchemy import text
from src.database import engine

def main():
    with engine.connect() as connection:
        result = connection.execute(
            text("SELECT version()")
        )

        print("Connected successfully!")
        print(result.scalar())

if __name__ == "__main__":
    main()
