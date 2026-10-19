# Aviator Intelligence Lab — Bet261

Laboratoire local pour enregistrer et étudier des résultats. Les fréquences
historiques ne prédisent pas le prochain multiplicateur et aucune stratégie ne
garantit un gain. Le projet ne réalise ni pari ni retrait.

## Environnement

Le dépôt contient déjà un environnement `.venv` en Python 3.14 et les
dépendances nécessaires. Dans un terminal VS Code ouvert à la racine :

```bash
source .venv/bin/activate
python --version
```

Pour créer l'environnement sur une nouvelle installation Ubuntu, installez
Python 3 et `venv`, puis lancez `python3 -m venv .venv` et
`.venv/bin/pip install -e . -r requirements-dev.txt`.

## Tableau de bord Streamlit

Depuis la racine du projet, activez l'environnement puis lancez :

```bash
streamlit run src/aviator_lab/dashboard/app.py
```

Le tableau de bord propose dix vues en français : historique et saisie/import,
distribution, probabilités empiriques, diagnostics, paris personnels,
simulations fictives, qualité et exportations. L'origine des observations se
choisit dans la barre latérale; la vue générale garde les trois origines en
colonnes séparées. La base reste dans `data/validated/`.

L'API locale et l'extension Firefox de diagnostic sont documentées dans
[`docs/firefox-collection.md`](docs/firefox-collection.md). Pour démarrer l'API
locale dans un premier terminal :

```bash
source .venv/bin/activate
python -m aviator_lab.collection.api
```

Dans un second terminal, démarrez le dashboard :

```bash
source .venv/bin/activate
streamlit run src/aviator_lab/dashboard/app.py
```

Le dashboard rafraîchit l'état toutes les cinq secondes et indique l'âge du
heartbeat et les erreurs récentes de l'API. **La détection automatique des
résultats Bet261 n'est pas activée** : le rendu de la page Aviator n'a pas pu
être inspecté depuis cet environnement géobloqué. L'extension de diagnostic ne
collecte aucun résultat; un heartbeat n'est pas une preuve de collecte.
L'installation temporaire dans Firefox est décrite dans le
[guide de collecte](docs/firefox-collection.md).

## Première fonctionnalité : stocker une manche

Depuis la racine du projet, activez `.venv`, puis saisissez par exemple :

```bash
python main.py add-round 2.35 --source "saisie manuelle"
python main.py add-round 1.42 --source "historique importé" --external-id manche-002
python main.py import data/raw/mon_historique.csv
python main.py import data/raw/mon_historique.xlsx --origin real
```

La base SQLite est créée dans `data/validated/aviator_lab.sqlite`. Les
multiplicateurs doivent être finis et supérieurs ou égaux à 1. Les identifiants
externes en double sont signalés. L'origine est `real` par défaut; les données
`synthetic` et `simulated` sont conservées séparément. Les données invalides
sont refusées explicitement, jamais supprimées silencieusement.

Les fichiers CSV et Excel (`.xlsx`, `.xlsm`) doivent contenir une colonne `multiplier`. Les
colonnes `external_id`, `played_at` (date ISO 8601 dans un CSV) et `origin`
sont facultatives. Le résumé indique les lignes ajoutées, les lignes rejetées
avec leur numéro et les lignes entièrement vides ignorées.

## Tests

```bash
python -m pytest
```

Les tests utilisent une base temporaire et des observations de test. Le
protocole d'ingestion locale est testé; la collecte de manches sur une session
Bet261 réelle reste à vérifier. Voir le [guide Firefox](docs/firefox-collection.md).

## Statistiques descriptives et probabilités empiriques

Les fonctions Python sont disponibles dans `aviator_lab.analytics` et
`aviator_lab.probability`. Sélectionnez une origine avant l'analyse pour garder
les résultats réels, synthétiques et simulés séparés :

```python
from aviator_lab import config
from aviator_lab.storage import RoundRepository
from aviator_lab.analytics import descriptive_stats
from aviator_lab.probability import empirical_exceedance_probability

repository = RoundRepository(config.DB_PATH)
rounds = repository.list_rounds(origin="real")
multipliers = [round_.multiplier for round_ in rounds]

summary = descriptive_stats(multipliers, origin="real")
estimate = empirical_exceedance_probability(multipliers, 2.0, origin="real")
print(summary.n_valid, summary.mean, summary.n_missing)
print(estimate.estimate, estimate.confidence_low, estimate.confidence_high)
repository.close()
```

L'intervalle de Wilson décrit l'incertitude d'une proportion si les manches
forment des essais indépendants et de probabilité stable. Ces hypothèses peuvent
ne pas tenir; une fréquence historique n'est pas une prédiction du prochain
multiplicateur. Les séquences sont séparées par toute valeur manquante.

## Diagnostics avancés

`aviator_lab.rng_research` propose Ljung–Box (autocorrélations jusqu'à des
retards choisis), un test approximatif des séquences au-dessus/au-dessous d'un
seuil, Mann–Whitney pour comparer deux périodes indépendantes et des corrections
Holm ou Benjamini–Hochberg quand plusieurs hypothèses sont testées.

Ces tests exigent des observations dans leur ordre réel et une origine unique.
Les diagnostics de dépendance refusent les valeurs manquantes plutôt que de
recoller des manches non consécutives. Le seuil du test de séquences est la
médiane par défaut; les égalités au seuil sont écartées. Ces choix et les tailles
des échantillons sont indiqués dans chaque résultat. Les tests ne déterminent
pas pourquoi une différence existe, ne confirment pas la distribution officielle
du jeu et ne démontrent pas un avantage pour miser. Aucun test d'adéquation à une
distribution théorique n'est activé sans modèle officiel documenté.

## Paris personnels et résultats financiers

Les paris peuvent être saisis manuellement ou importés avec `import-bets` :

```bash
python main.py add-bet --source "relevé personnel" --stake 10 \
  --cashout-multiplier 2 --returned 20 --currency MGA
python main.py import-bets data/raw/mes_paris.csv
```

Le fichier accepte les colonnes `external_id`, `round_id`, `bet_at`,
`stake_amount`, `cashout_multiplier`, `returned_amount`, `net_amount`,
`currency` et `origin`. Les montants manquants restent absents. Le résultat net
est calculé comme retour moins mise uniquement si ces deux montants sont fournis;
le multiplicateur de retrait ne sert jamais à inventer un retour. Si un résultat
net est fourni en plus de la mise et du retour, il doit être cohérent.

`financial_summary(repository.list_bets(origin="real"), origin="real")`
calcule les totaux des valeurs disponibles, les gains/pertes dont le net est
connu, les séries de pertes, la volatilité et, seulement si chaque net est connu,
la courbe de résultat net cumulé et le drawdown depuis un point de départ à zéro.
Chaque compteur précise combien de paris
contribuent à ses montants. Les montants sont conservés en décimal; aucune
conversion de devise n'est faite et une analyse refuse de sommer plusieurs
devises. La courbe suit l'ordre d'insertion de la base, à vérifier contre
l'ordre chronologique de vos données.
Dans le dashboard, le capital illustré est le capital initial saisi
volontairement plus la somme des nets connus; les dépôts, retraits et corrections
externes ne sont pas suivis.

## Simulations à argent fictif

Après avoir importé ou enregistré un historique, le bootstrap permet d'explorer
une mise fixe et un seuil de retrait hypothétique :

```bash
python main.py simulate --origin real --stake 1 --cashout-multiplier 2 \
  --bankroll 100 --rounds 100 --trials 1000 --loss-limit 25 --seed 42
```

Chaque trajectoire rééchantillonne avec remise les multiplicateurs de l'origine
choisie. Le modèle suppose un retour brut égal à `mise × seuil` lorsque le
multiplicateur atteint le seuil, zéro sinon; il ignore les frais et ne confirme
pas le barème réel du jeu. Il suppose aussi des manches IID et ne modélise pas
les dépendances temporelles. Le risque de ruine signifie ici que la bankroll
devient inférieure à la mise fixe nécessaire pour poursuivre. Les résultats
décrivent ces hypothèses seulement et ne garantissent aucun gain.
L'historique est réutilisé comme source du bootstrap; la simulation ne constitue
pas une évaluation hors échantillon indépendante.

La graine, les paramètres, la version du code et la période de référence sont
enregistrés localement dans la table SQLite `analysis_runs`. Les résultats de
simulation sont étiquetés `simulated`; ils ne sont pas ajoutés à l'historique
des manches réelles.

Dans la vue générale, l'état **DONNÉES INSUFFISANTES** est utilisé avant 100
manches valides. Au-delà, l'assistant indique **AUCUN SIGNAL FIABLE — NE PAS
MISER**; un diagnostic de dépendance exploratoire significatif après correction
de Holm à 1 % est expliqué comme un écart à examiner, sans jamais recommander un pari. Ce repère
d'échantillon et ces tests ne sont pas un modèle de rentabilité. Les données
marquées « réelles » ne sont pas pour autant vérifiées par Bet261.
