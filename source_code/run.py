"""Start the application.

    python run.py                 start on http://127.0.0.1:5000
    python run.py --port 8080     use another port
    python run.py --reset         delete the database and rebuild it with fresh demo data

On the first start the database is created from hotel/schema.sql and, in demo mode
(config.ini), filled with sample rooms, guests and about two months of history.
"""
import argparse

from hotel import create_app
from hotel.settings import Settings


def main() -> None:
    parser = argparse.ArgumentParser(description="Grand Horizon Hotel booking system")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--reset", action="store_true", help="delete the database and start from demo data")
    args = parser.parse_args()
    if args.reset:
        db = Settings.load().database_path()
        for path in (db, db.with_suffix(".db-wal"), db.with_suffix(".db-shm"), db.parent / "outbox.jsonl"):
            path.unlink(missing_ok=True)
        print(f"Database {db} removed; it will be rebuilt now.")
    create_app().run(host=args.host, port=args.port, debug=False)


if __name__ == "__main__":
    main()
