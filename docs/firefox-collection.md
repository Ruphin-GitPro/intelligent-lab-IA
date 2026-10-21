# Collecte Firefox locale

## Historique des paris affiché

L'extension lit les lignes présentes dans le DOM de l'historique : date affichée,
mise, multiplicateur de retrait et gain affichés en MGA. Elle les envoie à
l'API locale, qui les enregistre dans `personal_bets` et évite les doublons lors
des inspections répétées. Les champs vides restent inconnus; aucun gain ou
multiplicateur n'est déduit. La date de l'interface n'a qu'une précision à la
minute. Seules les lignes déjà chargées par la page sont visibles; faire défiler
ou paginer peut être nécessaire pour charger l'historique complet.

## État actuel et limite connue

Le projet avait SQLite, un dépôt de stockage, les analyses et le dashboard
Streamlit. Il n'avait ni extension Firefox, ni API locale, ni code de collecte.
La page `www.bet261.mg` est géobloquée depuis l'environnement de développement
utilisé pour ce changement, et aucun Firefox authentifié n'y est disponible.
Il est donc impossible d'établir ici si les résultats terminés d'Aviator sont
des nœuds DOM accessibles, du texte dans un canvas, une iframe, ou une autre
surface. Aucun sélecteur de jeu, endpoint privé, flux réseau ou règle de fin de
manche n'a été supposé.

## Audit de la chaîne actuelle

L'audit du 18 octobre 2026 confirme que la première rupture est avant l'API :
`extensions/firefox/content.js` construit un rapport DOM en lecture seule,
avec `extractionEnabled: false`. Il ne produit aucun événement de manche et
n'envoie aucun multiplicateur. Le background ne relaie que les diagnostics de
frames en mémoire et le heartbeat. Le heartbeat appelle
`POST /api/v1/heartbeat`; il ne transporte aucune donnée de manche.

Même si un client appelait `POST /api/v1/rounds`,
`src/aviator_lab/collection/api.py` répond actuellement `503 source_unverified`
à toute manche, car aucun adaptateur ne prouve à la fois l'origine et la
finalité. L'API n'appelle donc jamais `RoundRepository.add_round` pour cette
route. C'est pourquoi une absence de nouvelle ligne n'indique pas une erreur
SQLite ou un mauvais chemin dans ce flux : aucune insertion n'est tentée.

Les chemins existants concordent : `src/aviator_lab/config.py` définit
`DB_PATH` comme `data/validated/aviator_lab.sqlite` sous la racine du projet;
`RoundRepository` résout un chemin absolu, initialise le schéma SQLAlchemy et
encadre ses écritures par une transaction `Session.begin()`. Le dashboard
utilise ce même `config.DB_PATH`, relit les manches par origine et affiche un
état vide quand aucune manche n'existe. CSV/XLSX passent par les importeurs,
pas par l'extension.

La base présente lors de l'audit était
`/mnt/92A816A5A81687BD/D_M1_IA_ENI/IA & Automatisation/aviator-intelligence-lab/data/validated/aviator_lab.sqlite`;
la lecture SQLite en mode seule lecture a trouvé les tables `rounds`,
`personal_bets` et `analysis_runs`, et zéro ligne dans `rounds` (dont zéro
d'origine `real`). Les tests vérifient séparément le rejet API, la validation,
l'écriture/lecture du dépôt et la persistance après réouverture; ils ne
permettent pas de conclure sur une session Bet261.

Il n'y a pas de collecteur réseau/WebSocket, de client HTTP d'envoi de manche,
ni d'identifiant fiable de tour implémenté. Les deux multiplicateurs `3.55` et
`1.36` rapportés manuellement confirment seulement la présence de textes dans
un élément inspecté, pas leur provenance, leur stabilité après une manche, ni
un identifiant unique. Activer l'insertion maintenant transformerait une
observation ambiguë en prétendue donnée réelle; le garde-fou API reste donc
actif.

L'extension reste un diagnostic local, pas un collecteur de manches. Le manifeste
la lance à `document_idle` sur les pages Bet261 autorisées et aussi dans chaque
frame dont l'URL correspond aux mêmes motifs Bet261. Elle ne s'injecte pas dans
une iframe d'un autre domaine et n'en contourne pas l'accès depuis le parent.
Chaque rapport indique l'origine du document inspecté, s'il s'agit du frame
principal ou d'un frame ciblé, l'état `document.readyState`, le numéro de scan
et si les éléments du jeu étaient présents lors du scan.
Seul le frame principal affiche le panneau. Les diagnostics des frames Bet261
correspondants sont gardés temporairement en mémoire par l'extension et inclus
dans `embeddedFrameDiagnostics`; ces rapports expirent après 20 secondes et ne
sont envoyés ni à l'API Python ni à un service externe.

Le diagnostic inspecte séparément le composant déjà observé
`hg-aviator-llc-game.aviator-llc-game` et le conteneur d'historique
`._items_7l84e_35`; il ne suppose pas qu'ils soient parents. Le parcours couvre
les enfants DOM et Shadow DOM ouverts accessibles, jusqu'à 5 000 éléments, et
le rapport reste plafonné.
Pour les éléments pertinents, le JSON ne contient que balise, classes filtrées,
rôle/type filtrés, état d'exposition du Shadow DOM, chemin structurel réduit et
éventuelle chaîne numérique stricte. Les nombres simples sont marqués
`number_without_multiplier_marker` et restent ambigus; même `2.35x` n'est pas
une preuve de résultat final. Canvas et SVG sont comptés dans le composant, sans
lire leurs pixels.

Le diagnostic donne aussi l'index des iframes trouvées dans le DOM parcouru,
la présence de leur attribut `src` (sans son chemin complet), leur origine,
titre, `readyState` lorsque lisible, la correspondance aux motifs HTTPS Bet261
du manifeste, l'accessibilité, le type MIME et l'état d'inspection. Si le parent
peut lire un document de même origine, il inspecte ce document de façon bornée
pour le sélecteur observé et compte les textes numériques candidats. Pour une
frame inaccessible, le parent marque les
résultats comme inconnus; une frame Bet261 peut également fournir son propre
rapport distinct si Firefox y exécute le content script. Un iframe d'un autre
domaine est identifié seulement et n'est pas inspecté. Ces états ne sont pas
fusionnés comme s'ils provenaient du même document. Le diagnostic ne tente pas
de contourner la politique de même origine. Un ShadowRoot nul est rapporté
comme `not_exposed_absent_or_closed`, car un script
de page ne peut pas distinguer un Shadow DOM absent d'un Shadow DOM fermé. Le
rapport ne contient pas le HTML brut, le texte général, les valeurs de `src`,
les cookies, jetons, mots de passe ou informations de compte.

Le diagnostic réel fourni le 18 octobre 2026 inspectait 487 éléments du frame
principal, y trouvait zéro composant, zéro conteneur et zéro Shadow DOM ouvert;
il signalait cinq iframes, dont une inaccessible. Il décrit le frame principal,
pas le contenu des frames cross-origin. La structure transmise
`iframe → div.game → div.aviator-llc-game → hg-aviator-llc-game` indique que le
jeu observé peut se trouver dans un document d'iframe, mais ne donne pas
l'origine de cette frame ni un rapport d'inspection enfant. On ne peut donc pas
encore dire si le blocage est une frame tierce hors des motifs du manifeste, une
frame Bet261 qui n'a pas produit de rapport, ou un autre contexte du jeu.

Les mutations sont observées sur le document accessible et ses Shadow Roots
ouverts. Un scan initial est fait à l'injection, puis après mutation au plus
une fois par seconde (événements regroupés), avec un contrôle périodique toutes
les cinq secondes. Le rapport conserve les chemins modifiés, pas le texte des
mutations, et suit les candidats apparus/disparus entre scans. Cela ne détermine pas si une
valeur est évolutive, historique ou définitive. `completionSemantics` reste
inconnu, `extractionEnabled` reste `false` et aucune valeur n'est envoyée à
l'API ou SQLite.

### Historique DOM observé

Un utilisateur a fourni le HTML réel d'un élément de liste ayant la classe
`_items_7l84e_35`, avec deux boutons dont les textes étaient `3.55` et `1.36`.
L'extension inspecte maintenant ce conteneur exact partout dans le document
accessible et ses Shadow DOM ouverts. Elle relève les boutons enfants directs, leur position DOM
et leur texte si celui-ci respecte un décimal positif d'au plus quatre
décimales. Les autres textes sont comptés comme invalides et ne sont pas
transmis. Le diagnostic cherche aussi quelques attributs d'identifiant présents
sur les boutons; ils restent des candidats non interprétés, pas des identifiants
de manche validés.

À chaque rafraîchissement, les instantanés de la liste sont comparés pour
signaler une insertion apparente au début ou à la fin, un retrait, un
réordonnancement ou une évolution ambiguë; `lastChange` garde le dernier
changement observé visible même après les scans sans changement. Le premier instantané après le
chargement/rechargement est une référence, pas une série de nouvelles manches.
Si des valeurs sont identiques, la position seule ne permet pas toujours de
distinguer quel élément a été ajouté. Après un rechargement, le suivi repart de
cette référence; aucune déduplication n'est persistée parce qu'aucune clé de
manche fiable n'a été établie.

Ces fonctions restent des observations de DOM : aucune manche n'est envoyée à
l'API. Le HTML fourni établit que ces textes existaient dans un historique, mais
ne démontre pas qu'une nouvelle valeur correspond à un crash final, qu'elle
apparaît à un bord stable de la liste, ni qu'un bouton porte un identifiant de
manche. Il faut observer plusieurs transitions dans Firefox réel avant de
conclure sur l'ordre et la sémantique.

La collecte automatique demandée ne peut être déclarée terminée tant que la
page Aviator réelle n'a pas été examinée. Une extraction DOM est envisageable si
la liste des manches terminées expose un texte stable et distinguable du tour en
cours. Si le jeu ne rend les valeurs que dans un canvas, la solution DOM ne
pourra pas les lire; il faudra d'abord évaluer une interface d'accessibilité ou
une méthode OCR locale, autorisée par les conditions du site et vérifiée contre
les résultats affichés. Cette extension ne lit pas le trafic réseau, les
cookies, les jetons ou les variables internes du jeu.

## Étude de la connexion WebSocket

L'utilisateur observe qu'une connexion WebSocket apparaît dans Firefox pendant
le jeu. Cela établit seulement qu'une connexion temps réel est ouverte; cela ne
prouve ni que ses frames contiennent les multiplicateurs, ni qu'elles
représentent la fin d'une manche, ni qu'un historique complet soit diffusé.
L'inspection présente n'a observé aucun frame de cette session et n'a pas
capturé de trafic Bet261.

La documentation publique consultée de Spribe décrit Aviator comme un jeu où un
multiplicateur évolue pendant la manche et mentionne des statistiques de jeu
visibles, mais ne documente pas un flux WebSocket public pour les joueurs ou un
format de message stable. Le centre d'aide Bet261 répertorie des rubriques
générales de règles et résultats, sans spécification technique d'un flux
WebSocket Aviator. Les termes et conditions trouvés sur le domaine Corporate
Bet261 concernent explicitement le site corporate; ils ne constituent pas une
autorisation de consommer ou d'archiver le flux du jeu. Sources :
[description officielle d'Aviator par Spribe](https://spribe.co/games/aviator),
[centre d'aide Bet261](https://bet261sav.zendesk.com/hc/fr/categories/28002188421010-Demande-d-informations),
[rubrique Bet261 « Programmes et Résultats »](https://bet261sav.zendesk.com/hc/fr/sections/28002538547858-Programmes-et-R%C3%A9sultats),
[conditions d'utilisation du site Corporate Bet261](https://corporate.bet261.mg/termes-et-conditions-dutilisation/).

Des pages nommées « Aviator Studio » décrivent une API d'historique destinée à
des intégrations fournisseur et protégée par des identifiants fournisseur.
L'éditeur, le lien avec le jeu déployé sur Bet261 et l'autorisation d'accès
pour un joueur ne sont pas établis; cette API n'est donc pas utilisée comme
source pour ce projet.

Par conséquent, aucun format de frame, identifiant de manche, événement de fin,
ordre temporel ou lien frame/résultat ne peut être établi avec les preuves
disponibles. Aucun collecteur WebSocket n'est ajouté. Le code existant
n'intercepte pas la connexion observée et ne tente pas de la reproduire ou de
contourner une protection. Le support technique ou l'opérateur devrait d'abord
confirmer par écrit un moyen de consultation/archivage autorisé ou fournir un
export officiel des résultats. À défaut, les importeurs CSV/XLSX existants
restent disponibles et documentés; ils ne sont pas présentés comme une collecte
automatique.

## Origine de l'iframe du jeu (relevée le 18 octobre 2026)

L'onglet Réseau de Firefox, filtré sur HTML, montre que le document de l'iframe
du jeu est chargé depuis `crash.aviator.studio` (redirection 301) puis depuis un
sous-domaine de la forme `afN.crash.aviator.studio` (type `subdocument`). C'est
un domaine différent de Bet261 : avant ce changement, le manifeste n'injectait
donc pas le content script dans ce document, ce qui explique les zéro composant
du diagnostic précédent. Le manifeste couvre désormais `https://*.crash.aviator.studio/*`
(le sous-domaine peut changer d'une session à l'autre) et rien de plus large.

Ce changement ne fait qu'étendre le diagnostic en lecture seule à ce document.
`extractionEnabled` reste `false`, l'API répond toujours `503 source_unverified`,
et la connexion WebSocket observée n'est pas lue. Il faut recharger l'extension
dans `about:debugging`, puis actualiser l'onglet du jeu, pour que le rapport
`embeddedFrameDiagnostics` contienne un document `afN.crash.aviator.studio`.

## Ubuntu : démarrer l'application locale

Dans deux terminaux à la racine du dépôt :

```bash
source .venv/bin/activate
python -m aviator_lab.collection.api
```

Puis dans le second terminal :

```bash
source .venv/bin/activate
streamlit run src/aviator_lab/dashboard/app.py
```

L'API écoute uniquement sur `127.0.0.1:8765`. Le dashboard s'actualise toutes
les cinq secondes et signale l'état de l'API et de l'extension. Les résultats
sont dans `data/validated/aviator_lab.sqlite`.

## Installer l'extension locale dans Firefox

1. Ouvrir `about:debugging#/runtime/this-firefox`.
2. Choisir **Charger un module complémentaire temporaire…**.
3. Sélectionner `extensions/firefox/manifest.json` dans ce dépôt.
4. Si l'extension était déjà chargée, cliquer **Recharger** dans
   `about:debugging`, puis actualiser l'onglet du jeu.
5. Ouvrir Bet261 normalement puis Aviator. L'API n'est pas nécessaire au
   diagnostic local; elle ne reçoit que le heartbeat de présence.
6. Dans le panneau « Aviator Lab », cliquer sur **Actualiser le diagnostic**.
   Le rapport est aussi rafraîchi périodiquement et après des mutations dans le
   composant Aviator accessible.
7. Cliquer dans la zone JSON, faire `Ctrl+A`, `Ctrl+C`, puis coller le rapport
   dans un fichier local pour le consulter. L'icône et le panneau signalent
   séparément la connexion heartbeat à l'API.

Le module temporaire est retiré à la fermeture de Firefox; il faut alors le
charger à nouveau. Cette méthode est destinée au développement local.

Pour poursuivre l'analyse, transmettez les objets `documentContext`, `domScan`,
`aviatorComponents`, `multiplierHistories`, `historyEvolution`,
`embeddedFrameDiagnostics`,
`documents[0].iframes`, `inaccessibleFrameCount`, `inaccessibleFrameReasons`,
`domChangeCount`, `recentMutationTargets` et `multiplierCandidateChanges`.
Dans `documents[0]`, `actuallyInspected` indique que le document courant a été
parcouru; dans chaque iframe, `actuallyInspected: false` avec des compteurs
`null` signifie que le document a seulement été identifié, pas inspecté.
`selectorExists`, `matchCount` et `numericTextCount` décrivent le sélecteur
observé sans établir que ces nombres sont des résultats définitifs. Comparez
les frames séparément avec `embeddedFrameDiagnostics`; un chemin de mutation
qui se termine par `iframe` ne prouve pas que son document interne soit
accessible au parent.
`contentScriptExpectedByManifest` signifie seulement que l'origine HTTPS est
couverte par les motifs Bet261 du manifeste; `contentScriptStatus` précise si
un rapport enfant de cette origine a été reçu. Il ne peut pas associer ce
rapport à un index d'iframe avec certitude si plusieurs frames partagent la
même origine. Pour une origine tierce,
`outside_manifest_matches_no_injection_expected` reflète la portée volontaire
du manifeste, en plus de la barrière de même origine du navigateur. `loadState`
reste inconnu quand seul le document enfant pourrait le fournir.
`domChangeCount` compte les mutations DOM, pas des résultats ni des manches
terminées.
Vérifiez les candidats numériques avant partage, car un nombre simple pourrait
correspondre à une mise ou à une autre valeur de jeu. Le rapport n'inclut pas le
texte autour du nombre; ne joignez pas d'informations de compte.
Ne transmettez jamais mot de passe, cookie, jeton, solde, nom de compte ou
capture d'écran de la session.

### Permissions demandées

- Lecture/injection de contenu uniquement sur `https://bet261.mg/*` et ses
  sous-domaines HTTPS, y compris les frames qui correspondent à ces motifs,
  pour inspecter le DOM accessible et ses Shadow DOM ouverts.
- Accès HTTP uniquement à `http://127.0.0.1:8765/*` afin d'envoyer le heartbeat
  local et, une fois l'extracteur vérifié, les observations à l'API.
- Aucune permission `cookies`, `webRequest`, `tabs`, stockage, presse-papiers ou
  accès général à toutes les pages. Aucune connexion sortante vers un service
  externe.

## API locale

- `GET /health` et `GET /api/v1/status` : état, nombre de manches réelles,
  dernier multiplicateur stocké, heure d'enregistrement locale et erreurs API.
- `POST /api/v1/heartbeat` : présence de l'extension, sans donnée de jeu.
- `POST /api/v1/rounds` : refuse actuellement toute manche avec HTTP 503
  `source_unverified`. Le champ client `completed: true` ne démontre ni la
  provenance Bet261 ni la fin réelle d'une manche.

L'API n'écrit plus aucune manche entrante en SQLite tant qu'aucun adaptateur de
source autorisé n'est disponible. Le heartbeat ne met à jour que l'état de
connexion. La validation du stockage (`multiplier >= 1`, précision maximale et
unicité de `external_id`) protège les données fournies par les autres chemins
d'ingestion; elle n'atteste pas que leur provenance est Bet261. La déduplication
future devra utiliser un identifiant de manche stable, jamais le multiplicateur
seul.

## Vérifier l'enregistrement SQLite

L'API signale actuellement `source_unverified` et son endpoint de manche répond
503; il ne peut donc pas y avoir de nouvelle ligne créée par l'extension. Pour
inspecter localement les lignes déjà présentes :

```bash
sqlite3 data/validated/aviator_lab.sqlite \
  'SELECT id, external_id, multiplier, played_at, recorded_at, source, origin FROM rounds ORDER BY id DESC LIMIT 10;'
```

`origin = real` et `validation_status = valid` ne signifient pas « vérifié par
Bet261 » : le premier classe les données, le second valide leur forme. Le badge
de l'extension indique uniquement la réception d'un heartbeat et ne prouve pas
une collecte de manches.

`played_at` est l'heure de manche fournie par la source; `recorded_at` est
l'heure UTC à laquelle le programme a enregistré la ligne. Les lignes déjà
présentes avant cette migration conservent `recorded_at = NULL`, car leur heure
d'enregistrement passée ne peut pas être reconstruite.

Le statut inclut aussi l'âge du dernier heartbeat, l'heure d'enregistrement de
la dernière ligne et le compteur d'erreurs API,
la dernière erreur de validation et son heure UTC. Une nouvelle connexion par
heartbeat fait repasser l'état de connexion à actif; l'erreur précédente reste
affichée comme historique. Ces erreurs couvrent l'API locale, pas les erreurs
du jeu ou l'état de la connexion WebSocket Bet261.

## Tests locaux

```bash
python -m pytest tests/test_collection_api.py tests/test_storage.py
node --test extensions/firefox/tests/extension.test.cjs
```

Ces tests couvrent le protocole local, l'insertion SQLite, l'idempotence, la
validation, le refus des tours en cours et un test structurel de l'extension.
Ils ne valident pas la lecture d'une connexion WebSocket Bet261, ni
l'intégration avec le jeu en direct. Après obtention d'une méthode officielle
ou autorisée et accès à un environnement de test réel, il faudra vérifier le
format des messages, l'événement qui marque une manche terminée, la stabilité
de l'identifiant et l'absence de doublons sur plusieurs manches.

Pour poursuivre, transmettre le JSON du diagnostic et préciser si les valeurs
visibles changent pendant le vol ou apparaissent dans une liste après la fin
d'une manche. Ne joindre ni capture de compte ni valeur pouvant révéler un solde
ou une mise. Ne partager jamais mot de passe, cookie ou jeton de session.
