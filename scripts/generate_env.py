"""Generate a fresh local deployment secret file without displaying secrets."""
import argparse
from pathlib import Path
import secrets


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default=".env")
    args = parser.parse_args()
    destination = Path(args.output)
    # Exclusive creation prevents accidental secret rotation.
    with destination.open("x", encoding="utf-8") as stream:
        stream.write(
            f"LOGFORGE_SECRET_KEY={secrets.token_hex(32)}\n"
            "LOGFORGE_ENV=development\nDATABASE_URL=sqlite:///./data/logforge.db\n"
            "LOGFORGE_ALLOWED_ORIGINS=http://localhost:5173,http://127.0.0.1:5173\n"
            "LOGFORGE_MAX_ACTIVE_REQUESTS=32\n"
            "LOGFORGE_ALLOW_PRIVATE_WEBHOOKS=false\n"
        )
    try:
        destination.chmod(0o600)
    except OSError:
        pass
    print(f"Created {destination}; keep it private and back it up securely.")


if __name__ == "__main__":
    main()
