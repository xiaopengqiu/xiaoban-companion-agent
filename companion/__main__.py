"""Run `python -m companion`; remote exposure requires explicit configuration."""
import os
from pathlib import Path
import uvicorn
from .app import create_app


def main():
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent/'.env',override=False)
    app=create_app(start_scheduler=os.getenv('COMPANION_SCHEDULER_ENABLED','true').lower()=='true')
    uvicorn.run(app,host=os.getenv('COMPANION_HOST','127.0.0.1'),port=int(os.getenv('COMPANION_PORT','8765')))


if __name__=='__main__':
    main()
