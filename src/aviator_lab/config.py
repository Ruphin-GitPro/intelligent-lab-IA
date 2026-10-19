"""Configuration centrale du projet.

Aucune donnée sensible ici : jamais d'identifiant, de mot de passe ni de cookie.
"""
from pathlib import Path

# Racine du projet (dossier contenant data/, src/, tests/...)
PROJECT_ROOT = Path(__file__).resolve().parents[2]

DATA_DIR = PROJECT_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"              # fichiers importés, jamais modifiés
VALIDATED_DIR = DATA_DIR / "validated"  # base SQLite (données validées)
SIMULATED_DIR = DATA_DIR / "simulated"  # sorties du simulateur (jamais mélangées au réel)
SAMPLES_DIR = DATA_DIR / "samples"      # jeux SYNTHÉTIQUES (préfixe "synthetic_")
EXPORTS_DIR = PROJECT_ROOT / "exports"
REPORTS_DIR = PROJECT_ROOT / "reports"

DB_PATH = VALIDATED_DIR / "aviator_lab.sqlite"

# Seuils d'analyse configurables (multiplicateurs)
DEFAULT_THRESHOLDS = (1.5, 2.0, 3.0, 5.0, 10.0, 20.0)

# Aucun RTP théorique n'est activé sans source documentaire fiable.
HYPOTHETICAL_RTP = None

# Niveau de confiance par défaut des intervalles
CONFIDENCE_LEVEL = 0.95

# Origine des observations, distincte du moyen de collecte (source).
ORIGINS = ("real", "synthetic", "simulated")
