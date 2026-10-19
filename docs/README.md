# Guide technique

Ce document complète le guide de démarrage du [README principal](../README.md).
Le projet est local; aucune source distante, connexion Bet261 ou transaction
n'est configurée.

## Arborescence utile

- `src/aviator_lab/storage/` : modèles SQLAlchemy et dépôt SQLite.
- `src/aviator_lab/ingestion/` : import CSV/Excel avec rapport des lignes rejetées.
- `src/aviator_lab/analytics/` : statistiques descriptives et résumé financier.
- `src/aviator_lab/probability/` : fréquences empiriques et intervalle de Wilson.
- `src/aviator_lab/rng_research/` : diagnostics exploratoires et correction multiple.
- `src/aviator_lab/simulation/` : bootstrap fictif, séparé des résultats observés.
- `src/aviator_lab/dashboard/` : application Streamlit en français.
- `tests/` : jeux de données synthétiques et tests sur bases temporaires.

Les dossiers `data/raw/`, `data/validated/`, `data/simulated/`, `exports/` et
`reports/` sont prévus pour les données et sorties locales. Les fichiers
personnels et bases SQLite sont ignorés par Git.

## Schéma SQLite

### `rounds`

Contient l'identifiant interne, l'identifiant externe facultatif, le
multiplicateur validé, l'heure du tour (`played_at`), l'heure d'enregistrement
locale (`recorded_at`), la source, l'origine et le statut de validation. Une
valeur invalide n'est pas insérée; les doublons d'identifiants externes sont
signalés. La migration ajoute `recorded_at` sans modifier les lignes existantes;
leur heure passée inconnue reste `NULL`.

### `personal_bets`

Contient une clé interne, un identifiant externe facultatif, un lien facultatif
vers une manche, la date, la mise, le multiplicateur de retrait, le montant
retourné, le résultat net, le code devise, la source et l'origine. Les champs
financiers manquants restent SQL `NULL`. Quand mise et retour sont fournis, le
net peut être calculé par `retour - mise`; un multiplicateur de retrait seul ne
permet jamais de déduire le retour.

### `analysis_runs`

Enregistre le type d'analyse, la période, la version du code, la graine
aléatoire et les paramètres JSON. Les paramètres d'une simulation lancée depuis
le CLI ou le tableau de bord y sont ajoutés.

Les origines permises sont `real`, `synthetic` et `simulated`. Le moyen de
collecte est conservé séparément dans le champ `source`.

## Formats d'import

Les deux importeurs lisent `.csv`, `.xlsx` et `.xlsm`, avec la première ligne
comme en-tête. Les en-têtes ne sont pas sensibles à la casse.

Historique des manches :

```csv
multiplier,external_id,played_at,origin
1.42,round-001,2026-10-18T12:30:00,real
```

Paris personnels :

```csv
external_id,round_id,bet_at,stake_amount,cashout_multiplier,returned_amount,net_amount,currency,origin
bet-001,1,2026-10-18T12:31:00,10,2,20,10,MGA,real
bet-002,,,,2,,,,real
```

Les lignes sans données sont comptées comme vides; les lignes partiellement
remplies sont conservées si elles satisfont les validations. Le rapport
d'import donne les numéros et motifs des lignes rejetées. Une ligne rejetée ne
bloque pas les autres.

Le tableau financier calcule les mises et retours uniquement à partir des
champs fournis. Le solde illustré est `capital initial saisi + somme des nets`
et ne comprend pas les dépôts, retraits ou corrections externes. Il n'est
affiché que si tous les résultats nets sont connus; l'ordre d'insertion doit
correspondre à l'ordre des paris pour interpréter sa courbe.

## Hypothèses statistiques

- La probabilité empirique compte les manches `M >= x`; son intervalle de Wilson
  décrit une proportion binomiale sous hypothèses d'indépendance et de stabilité.
- Ljung–Box teste des autocorrélations jusqu'aux retards choisis. Le test des
  séquences utilise un seuil explicite ou la médiane et écarte les égalités.
- Mann–Whitney compare deux échantillons indépendants. La sélection a posteriori
  des périodes et la dépendance temporelle peuvent invalider les p-values.
- Les tests ne confirment pas le modèle officiel du générateur et une détection
  ne prouve ni prédictibilité ni avantage économique.
- Aucun RTP n'est configuré par défaut. Une formule théorique ne doit être
  utilisée qu'avec ses hypothèses et sa provenance documentées.

## Hypothèse du simulateur

Le bootstrap rééchantillonne avec remise les résultats de référence, donc
suppose des observations IID. Le simulateur applique une règle illustrative :
si le multiplicateur atteint le retrait, retour brut `mise × retrait`; sinon,
retour nul. Les frais sont ignorés. Ce n'est pas une confirmation des règles
officielles et ce n'est pas une validation hors échantillon. Les comparaisons
doivent être interprétées comme scénarios sous hypothèses, jamais comme conseils
de mise.

## Vérifications locales

```bash
source .venv/bin/activate
python -m pytest
ruff check src main.py tests
```

Ruff est déclaré dans `requirements-dev.txt`. S'il n'est pas encore installé
dans l'environnement courant, installez les dépendances de développement avec
`.venv/bin/pip install -e . -r requirements-dev.txt` avant de lancer cette
commande.
