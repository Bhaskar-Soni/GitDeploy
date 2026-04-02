"""Seed the install_templates table with known stack commands."""

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db.database import get_sync_session
from db.models import InstallTemplate

logger = logging.getLogger(__name__)

TEMPLATES = [
    {"stack": "node", "commands": ["npm install", "npm run build"], "confidence": 0.8},
    {"stack": "python-pip", "commands": ["pip install -r requirements.txt"], "confidence": 0.8},
    {"stack": "python-poetry", "commands": ["poetry install"], "confidence": 0.85},
    {"stack": "rust", "commands": ["cargo build --release"], "confidence": 0.85},
    {"stack": "go", "commands": ["go mod download", "go build ./..."], "confidence": 0.85},
    {"stack": "java-maven", "commands": ["mvn install -DskipTests"], "confidence": 0.75},
    {"stack": "java-gradle", "commands": ["./gradlew build"], "confidence": 0.75},
    {"stack": "ruby", "commands": ["bundle install"], "confidence": 0.8},
    {"stack": "php", "commands": ["composer install"], "confidence": 0.8},
    {"stack": "elixir", "commands": ["mix deps.get", "mix compile"], "confidence": 0.8},
]


def seed():
    with get_sync_session() as session:
        for tpl in TEMPLATES:
            existing = session.query(InstallTemplate).filter_by(stack=tpl["stack"]).first()
            if not existing:
                session.add(InstallTemplate(**tpl))
                logger.info("Seeded: %s", tpl['stack'])
            else:
                logger.debug("Exists: %s", tpl['stack'])
        session.commit()
    logger.info("Seeding complete.")


if __name__ == "__main__":
    seed()
