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

# HYPOTHÈSE H0 (à vérifier dans la documentation officielle) :
# taux de retour au joueur (RTP) théorique. Ce n'est PAS une vérité établie.
HYPOTHETICAL_RTP = 0.97

# Niveau de confiance par défaut des intervalles
CONFIDENCE_LEVEL = 0.95

# Provenance possible des données (affichée dans le tableau de bord)
ORIGINS = ("game", "manual", "import", "simulation")
