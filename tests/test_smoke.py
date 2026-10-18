"""Test de fumée : vérifie que le squelette est correctement installé."""
from aviator_lab import __version__, config


def test_version_is_defined():
    assert __version__ == "0.1.0"


def test_thresholds_are_sorted_and_above_one():
    t = config.DEFAULT_THRESHOLDS
    assert list(t) == sorted(t)
    assert all(x > 1 for x in t)


def test_data_dirs_exist():
    for d in (config.RAW_DIR, config.VALIDATED_DIR, config.SIMULATED_DIR, config.SAMPLES_DIR):
        assert d.is_dir(), f"dossier manquant : {d}"
