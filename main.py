"""Point d'entrée en ligne de commande (sera enrichi aux phases suivantes)."""
from aviator_lab import __version__, config


def main() -> None:
    print(f"Aviator Intelligence Lab v{__version__}")
    print(f"Racine du projet : {config.PROJECT_ROOT}")
    print(f"Base de données  : {config.DB_PATH}")
    print(f"Seuils par défaut: {config.DEFAULT_THRESHOLDS}")
    print("Mode : lecture seule, aucune mise réelle.")


if __name__ == "__main__":
    main()
