"""Commandes locales simples pour initialiser et alimenter le laboratoire."""
import argparse

from aviator_lab import __version__, config
from aviator_lab.ingestion.importers import ImportFileError, import_bets, import_rounds
from aviator_lab.simulation import bootstrap_simulation
from aviator_lab.storage import RoundRepository


def main() -> None:
    parser = argparse.ArgumentParser(description="Aviator Intelligence Lab — Bet261")
    commands = parser.add_subparsers(dest="command")
    add_parser = commands.add_parser("add-round", help="enregistrer une manche validée")
    add_parser.add_argument("multiplier", help="multiplicateur final, par exemple 2.35")
    add_parser.add_argument("--source", required=True, help="origine de la saisie ou du fichier")
    add_parser.add_argument("--external-id", help="identifiant externe facultatif")
    add_parser.add_argument(
        "--origin", choices=config.ORIGINS, default="real", help="real, synthetic ou simulated"
    )
    import_parser = commands.add_parser("import", help="importer un fichier CSV ou Excel")
    import_parser.add_argument("file", help="chemin vers un fichier .csv ou .xlsx")
    import_parser.add_argument(
        "--origin", choices=config.ORIGINS, default="real", help="origine par défaut des lignes"
    )
    bet_parser = commands.add_parser("add-bet", help="enregistrer un pari personnel")
    bet_parser.add_argument("--source", required=True, help="source des données")
    bet_parser.add_argument("--external-id", help="identifiant du pari facultatif")
    bet_parser.add_argument("--round-id", type=int, help="identifiant interne de manche facultatif")
    bet_parser.add_argument("--stake", help="montant misé fourni")
    bet_parser.add_argument("--cashout-multiplier", help="multiplicateur de retrait choisi")
    bet_parser.add_argument("--returned", help="montant effectivement retourné fourni")
    bet_parser.add_argument("--net", help="résultat net fourni")
    bet_parser.add_argument("--currency", help="code devise facultatif, par exemple MGA")
    bet_parser.add_argument(
        "--origin", choices=config.ORIGINS, default="real", help="origine des données"
    )
    import_bets_parser = commands.add_parser("import-bets", help="importer des paris CSV ou Excel")
    import_bets_parser.add_argument("file", help="chemin vers un fichier .csv ou .xlsx")
    import_bets_parser.add_argument(
        "--origin", choices=config.ORIGINS, default="real", help="origine par défaut des lignes"
    )
    simulation_parser = commands.add_parser("simulate", help="simuler une stratégie fictive")
    simulation_parser.add_argument(
        "--origin", choices=config.ORIGINS, default="real", help="origine de l'historique de référence"
    )
    simulation_parser.add_argument("--stake", type=float, required=True, help="mise fictive fixe")
    simulation_parser.add_argument(
        "--cashout-multiplier", type=float, required=True, help="seuil de retrait hypothétique"
    )
    simulation_parser.add_argument(
        "--bankroll", type=float, required=True, help="bankroll initiale fictive"
    )
    simulation_parser.add_argument("--rounds", type=int, default=100, help="manches max par essai")
    simulation_parser.add_argument("--trials", type=int, default=1000, help="nombre d'essais Monte-Carlo")
    simulation_parser.add_argument("--loss-limit", type=float, help="limite de perte fictive")
    simulation_parser.add_argument("--seed", type=int, default=42, help="graine aléatoire reproductible")
    args = parser.parse_args()

    if args.command in {"add-round", "import", "add-bet", "import-bets", "simulate"}:
        repository = RoundRepository(config.DB_PATH)
        try:
            repository.initialize()
            if args.command == "add-round":
                record = repository.add_round(
                    args.multiplier,
                    source=args.source,
                    external_id=args.external_id,
                    origin=args.origin,
                )
                print(f"Manche #{record.id} enregistrée : {record.multiplier}x ({record.origin}).")
            elif args.command == "add-bet":
                record = repository.add_bet(
                    source=args.source,
                    origin=args.origin,
                    external_id=args.external_id,
                    round_id=args.round_id,
                    stake_amount=args.stake,
                    cashout_multiplier=args.cashout_multiplier,
                    returned_amount=args.returned,
                    net_amount=args.net,
                    currency=args.currency,
                )
                print(f"Pari #{record.id} enregistré ({record.origin}); aucune mise n'a été placée.")
            elif args.command in {"import", "import-bets"}:
                importer = import_bets if args.command == "import-bets" else import_rounds
                report = importer(args.file, repository, default_origin=args.origin)
                print(
                    f"Import terminé : {report.imported} ligne(s) ajoutée(s), "
                    f"{len(report.rejected)} rejetée(s), {report.skipped_empty} vide(s)."
                )
                for issue in report.rejected:
                    print(f"Ligne {issue.row_number} : {issue.message}")
            else:
                rounds = repository.list_rounds(origin=args.origin)
                result = bootstrap_simulation(
                    [round_.multiplier for round_ in rounds],
                    reference_origin=args.origin,
                    stake=args.stake,
                    cashout_multiplier=args.cashout_multiplier,
                    starting_bankroll=args.bankroll,
                    max_rounds=args.rounds,
                    trials=args.trials,
                    loss_limit=args.loss_limit,
                    seed=args.seed,
                )
                timestamps = [round_.played_at for round_ in rounds if round_.played_at]
                repository.record_analysis(
                    analysis_type="bootstrap_simulation",
                    code_version=__version__,
                    random_seed=args.seed,
                    period_start=min(timestamps) if timestamps else None,
                    period_end=max(timestamps) if timestamps else None,
                    parameters={
                        "reference_origin": args.origin,
                        "reference_round_count": len(rounds),
                        "stake": args.stake,
                        "cashout_multiplier": args.cashout_multiplier,
                        "starting_bankroll": args.bankroll,
                        "max_rounds": args.rounds,
                        "trials": args.trials,
                        "loss_limit": args.loss_limit,
                        "payout_assumption": result.payout_assumption,
                    },
                )
                print(
                    f"Simulation fictive ({result.trials} essais, graine {result.seed}) : "
                    f"résultat net moyen {result.mean_net_result:.2f}, "
                    f"risque de ruine estimé {result.ruin_probability:.1%}, "
                    f"drawdown moyen {result.mean_max_drawdown:.2f}."
                )
                print("Paramètres enregistrés dans la table analysis_runs.")
        except (ImportFileError, ValueError) as exc:
            parser.error(str(exc))
        finally:
            repository.close()
    else:
        print(f"Aviator Intelligence Lab v{__version__}")
        print(f"Racine du projet : {config.PROJECT_ROOT}")
        print(f"Base de données  : {config.DB_PATH}")
        print("Aucun RTP théorique configuré; aucune mise réelle n'est effectuée.")
        parser.print_help()


if __name__ == "__main__":
    main()
